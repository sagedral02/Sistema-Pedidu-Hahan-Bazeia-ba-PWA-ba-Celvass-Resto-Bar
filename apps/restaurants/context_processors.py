from .branding import brand_context
from .models import Restaurant


def restaurant_context(request):
    try:
        restaurant = Restaurant.objects.filter(is_active=True).first()
        if not restaurant:
            restaurant = Restaurant(name="Restaurante Sabor Dili", currency="USD")
    except Exception:
        restaurant = Restaurant(name="Restaurante Sabor Dili", currency="USD")

    return {
        'current_restaurant': restaurant,
        'currency_symbol': '$' if restaurant.currency == 'USD' else restaurant.currency,
        'brand': brand_context(restaurant),
    }
