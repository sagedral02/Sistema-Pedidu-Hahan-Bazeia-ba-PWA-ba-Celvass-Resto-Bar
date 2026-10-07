from decimal import Decimal
import secrets
from django.db import transaction
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from .models import Category, MenuItem, ItemAvailability
from apps.restaurants.models import Restaurant
from apps.audit.services import log_audit_event


def generate_unique_category_slug(restaurant: Restaurant, base_name: str, current_id=None) -> str:
    base_slug = slugify(base_name) or "kategoria"
    candidate = base_slug
    counter = 1
    while Category.objects.filter(restaurant=restaurant, slug=candidate).exclude(id=current_id).exists():
        candidate = f"{base_slug}-{counter}"
        counter += 1
    return candidate


def generate_unique_menu_slug(restaurant: Restaurant, base_name: str, current_id=None) -> str:
    base_slug = slugify(base_name) or "menu-item"
    candidate = base_slug
    counter = 1
    while MenuItem.objects.filter(restaurant=restaurant, slug=candidate).exclude(id=current_id).exists():
        candidate = f"{base_slug}-{counter}"
        counter += 1
    return candidate


def create_category(
    *,
    restaurant: Restaurant,
    name: str,
    slug: str = None,
    description: str = "",
    icon_name: str = "utensils",
    sort_order: int = 0,
    is_active: bool = True,
    actor_user=None,
    request_id: str = ""
) -> Category:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Naran kategoria obrigatóriu atu preenxe.")

    with transaction.atomic():
        if slug:
            final_slug = slugify(slug.strip())
            if Category.objects.filter(restaurant=restaurant, slug=final_slug).exists():
                final_slug = generate_unique_category_slug(restaurant, final_slug)
        else:
            final_slug = generate_unique_category_slug(restaurant, name)

        category = Category.objects.create(
            restaurant=restaurant,
            name=name,
            slug=final_slug,
            description=description.strip() if description else "",
            icon_name=icon_name.strip() if icon_name else "utensils",
            sort_order=int(sort_order or 0),
            is_active=bool(is_active)
        )

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='CATEGORY_CREATE',
            entity_type='Category',
            entity_id=str(category.id),
            after_data={
                'name': category.name,
                'slug': category.slug,
                'icon_name': category.icon_name,
                'is_active': category.is_active
            },
            request_id=request_id
        )

        return category


def update_category(
    *,
    category: Category,
    name: str = None,
    slug: str = None,
    description: str = None,
    icon_name: str = None,
    sort_order: int = None,
    is_active: bool = None,
    actor_user=None,
    request_id: str = ""
) -> Category:
    with transaction.atomic():
        locked_cat = Category.objects.select_for_update().get(id=category.id)
        before_data = {
            'name': locked_cat.name,
            'slug': locked_cat.slug,
            'icon_name': locked_cat.icon_name,
            'sort_order': locked_cat.sort_order,
            'is_active': locked_cat.is_active
        }

        update_fields = ['updated_at']

        if name is not None:
            name_clean = name.strip()
            if not name_clean:
                raise ValidationError("Naran kategoria labele mamuk.")
            locked_cat.name = name_clean
            update_fields.append('name')
            if slug is None and not locked_cat.slug:
                locked_cat.slug = generate_unique_category_slug(locked_cat.restaurant, name_clean, current_id=locked_cat.id)
                update_fields.append('slug')

        if slug is not None:
            slug_clean = slugify(slug.strip())
            if slug_clean:
                locked_cat.slug = generate_unique_category_slug(locked_cat.restaurant, slug_clean, current_id=locked_cat.id)
                update_fields.append('slug')

        if description is not None:
            locked_cat.description = description.strip()
            update_fields.append('description')

        if icon_name is not None:
            locked_cat.icon_name = icon_name.strip() or "utensils"
            update_fields.append('icon_name')

        if sort_order is not None:
            locked_cat.sort_order = int(sort_order)
            update_fields.append('sort_order')

        if is_active is not None:
            locked_cat.is_active = bool(is_active)
            update_fields.append('is_active')

        locked_cat.save(update_fields=update_fields)

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='CATEGORY_UPDATE',
            entity_type='Category',
            entity_id=str(locked_cat.id),
            before_data=before_data,
            after_data={
                'name': locked_cat.name,
                'slug': locked_cat.slug,
                'icon_name': locked_cat.icon_name,
                'sort_order': locked_cat.sort_order,
                'is_active': locked_cat.is_active
            },
            request_id=request_id
        )

        return locked_cat


def delete_category(*, category: Category, actor_user=None, request_id: str = "") -> bool:
    with transaction.atomic():
        locked_cat = Category.objects.select_for_update().get(id=category.id)
        item_count = locked_cat.items.count()
        if item_count > 0:
            raise ValidationError(
                f"Kategoria '{locked_cat.name}' sei iha menu {item_count}. Favór muda ka hamos uluk menu sira molok hamos kategoria ne'e."
            )

        cat_id = str(locked_cat.id)
        cat_name = locked_cat.name
        locked_cat.delete()

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='CATEGORY_DELETE',
            entity_type='Category',
            entity_id=cat_id,
            before_data={'name': cat_name},
            request_id=request_id
        )
        return True


def create_menu_item(
    *,
    restaurant: Restaurant,
    category: Category,
    name: str,
    price: Decimal,
    slug: str = None,
    sku: str = "",
    description: str = "",
    image=None,
    image_url: str = "",
    availability: str = ItemAvailability.AVAILABLE,
    is_featured: bool = False,
    sort_order: int = 0,
    preparation_note: str = "",
    actor_user=None,
    request_id: str = ""
) -> MenuItem:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Naran menu obrigatóriu atu preenxe.")

    price_dec = Decimal(str(price)).quantize(Decimal('0.01'))
    if price_dec < Decimal('0.00'):
        raise ValidationError("Presu labele menus husi $0.00.")

    with transaction.atomic():
        if slug:
            final_slug = slugify(slug.strip())
            if MenuItem.objects.filter(restaurant=restaurant, slug=final_slug).exists():
                final_slug = generate_unique_menu_slug(restaurant, final_slug)
        else:
            final_slug = generate_unique_menu_slug(restaurant, name)

        item = MenuItem.objects.create(
            restaurant=restaurant,
            category=category,
            name=name,
            slug=final_slug,
            sku=sku.strip() if sku else "",
            description=description.strip() if description else "",
            price=price_dec,
            image=image if image else None,
            image_url=image_url.strip() if image_url else "",
            availability=availability or ItemAvailability.AVAILABLE,
            is_featured=bool(is_featured),
            sort_order=int(sort_order or 0),
            preparation_note=preparation_note.strip() if preparation_note else ""
        )

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='MENU_CREATE',
            entity_type='MenuItem',
            entity_id=str(item.id),
            after_data={
                'name': item.name,
                'category': category.name,
                'price': str(item.price),
                'availability': item.availability
            },
            request_id=request_id
        )

        return item


def update_menu_item(
    *,
    item: MenuItem,
    name: str = None,
    category: Category = None,
    price: Decimal = None,
    slug: str = None,
    sku: str = None,
    description: str = None,
    image=None,
    image_url: str = None,
    availability: str = None,
    is_featured: bool = None,
    sort_order: int = None,
    preparation_note: str = None,
    actor_user=None,
    request_id: str = ""
) -> MenuItem:
    with transaction.atomic():
        locked_item = MenuItem.objects.select_for_update().get(id=item.id)
        before_data = {
            'name': locked_item.name,
            'category_id': str(locked_item.category_id),
            'price': str(locked_item.price),
            'availability': locked_item.availability,
            'is_featured': locked_item.is_featured
        }

        update_fields = ['updated_at']

        if name is not None:
            name_clean = name.strip()
            if not name_clean:
                raise ValidationError("Naran menu labele mamuk.")
            locked_item.name = name_clean
            update_fields.append('name')
            if slug is None and not locked_item.slug:
                locked_item.slug = generate_unique_menu_slug(locked_item.restaurant, name_clean, current_id=locked_item.id)
                update_fields.append('slug')

        if slug is not None:
            slug_clean = slugify(slug.strip())
            if slug_clean:
                locked_item.slug = generate_unique_menu_slug(locked_item.restaurant, slug_clean, current_id=locked_item.id)
                update_fields.append('slug')

        if category is not None:
            locked_item.category = category
            update_fields.append('category')

        if price is not None:
            price_dec = Decimal(str(price)).quantize(Decimal('0.01'))
            if price_dec < Decimal('0.00'):
                raise ValidationError("Presu labele menus husi $0.00.")
            locked_item.price = price_dec
            update_fields.append('price')

        if sku is not None:
            locked_item.sku = sku.strip()
            update_fields.append('sku')

        if description is not None:
            locked_item.description = description.strip()
            update_fields.append('description')

        if image is not None:
            locked_item.image = image
            update_fields.append('image')

        if image_url is not None:
            locked_item.image_url = image_url.strip()
            update_fields.append('image_url')

        if availability is not None:
            locked_item.availability = availability
            update_fields.append('availability')

        if is_featured is not None:
            locked_item.is_featured = bool(is_featured)
            update_fields.append('is_featured')

        if sort_order is not None:
            locked_item.sort_order = int(sort_order)
            update_fields.append('sort_order')

        if preparation_note is not None:
            locked_item.preparation_note = preparation_note.strip()
            update_fields.append('preparation_note')

        locked_item.save(update_fields=update_fields)

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='MENU_UPDATE',
            entity_type='MenuItem',
            entity_id=str(locked_item.id),
            before_data=before_data,
            after_data={
                'name': locked_item.name,
                'category_id': str(locked_item.category_id),
                'price': str(locked_item.price),
                'availability': locked_item.availability,
                'is_featured': locked_item.is_featured
            },
            request_id=request_id
        )

        return locked_item


def delete_menu_item(*, item: MenuItem, actor_user=None, request_id: str = "") -> bool:
    with transaction.atomic():
        locked_item = MenuItem.objects.select_for_update().get(id=item.id)
        item_id = str(locked_item.id)
        item_name = locked_item.name

        # If referenced in OrderItem, safe soft-delete to preserve billing and historical orders
        has_orders = locked_item.order_items.exists()
        if has_orders:
            locked_item.availability = ItemAvailability.INACTIVE
            locked_item.save(update_fields=['availability', 'updated_at'])
            action = 'MENU_DEACTIVATE'
        else:
            locked_item.delete()
            action = 'MENU_DELETE'

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action=action,
            entity_type='MenuItem',
            entity_id=item_id,
            before_data={'name': item_name, 'soft_deactivated': has_orders},
            request_id=request_id
        )

        return {
            'deleted': True,
            'soft_deleted': has_orders,
            'message': "Menu deativadu ba INACTIVE tanba iha istóriku pedidu." if has_orders else "Menu hamos ho susesu!"
        }


def set_menu_item_availability(*, item: MenuItem, availability: str, actor_user=None, request_id: str = "") -> MenuItem:
    with transaction.atomic():
        locked_item = MenuItem.objects.select_for_update().get(id=item.id)
        old_val = locked_item.availability
        locked_item.availability = availability
        locked_item.save(update_fields=['availability', 'updated_at'])

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'STAFF',
            action='MENU_AVAILABILITY_CHANGE',
            entity_type='MenuItem',
            entity_id=str(locked_item.id),
            before_data={'availability': old_val},
            after_data={'availability': availability},
            request_id=request_id
        )
        return locked_item


def update_menu_item_price(*, item: MenuItem, new_price: Decimal, actor_user=None, request_id: str = "") -> MenuItem:
    with transaction.atomic():
        locked_item = MenuItem.objects.select_for_update().get(id=item.id)
        old_price = str(locked_item.price)
        locked_item.price = new_price
        locked_item.save(update_fields=['price', 'updated_at'])

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='MENU_PRICE_CHANGE',
            entity_type='MenuItem',
            entity_id=str(locked_item.id),
            before_data={'price': old_price},
            after_data={'price': str(new_price)},
            request_id=request_id
        )
        return locked_item
