from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from .models import User, Role

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_admin:
            return redirect('admin-portal')
        elif request.user.is_cashier:
            return redirect('cashier-portal')
        elif request.user.is_kitchen:
            return redirect('kitchen-portal')
        return redirect('cashier-portal')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                if user.is_admin:
                    return redirect('admin-portal')
                elif user.is_cashier:
                    return redirect('cashier-portal')
                elif user.is_kitchen:
                    return redirect('kitchen-portal')
                return redirect('cashier-portal')
            else:
                messages.error(request, 'Ita-nia konta la ativu.')
        else:
            messages.error(request, 'Naran-uzuáriu ka liafuan xave sala.')

    return render(request, 'auth/login.html')

def demo_login_view(request, role):
    """
    Convenient 1-click login for demonstration / evaluation.
    """
    user = None
    if role.upper() == 'ADMIN':
        user = User.objects.filter(role=Role.ADMIN, is_active=True).first()
    elif role.upper() == 'CASHIER':
        user = User.objects.filter(role=Role.CASHIER, is_active=True).first()
    elif role.upper() == 'KITCHEN':
        user = User.objects.filter(role=Role.KITCHEN, is_active=True).first()

    if user:
        login(request, user)
        if user.is_admin:
            return redirect('admin-portal')
        elif user.is_cashier:
            return redirect('cashier-portal')
        elif user.is_kitchen:
            return redirect('kitchen-portal')
            
    messages.error(request, f'Konta demo {role} seidauk rejistu.')
    return redirect('login')

def logout_view(request):
    logout(request)
    response = redirect('login')
    # Drop HTTP-cached staff pages on shared devices; the page script also clears the SW page cache.
    response['Clear-Site-Data'] = '"cache"'
    return response
