from django.contrib import admin
from django.urls import path, include, re_path, reverse_lazy
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),
    
    # Autenticación
    path('login/', auth_views.LoginView.as_view(
        template_name='bingo_app/login.html',
        redirect_authenticated_user=False,
        next_page='lobby'
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page=reverse_lazy('login')
    ), name='logout'),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='bingo_app/password_reset.html'
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='bingo_app/password_reset_done.html'
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='bingo_app/password_reset_confirm.html'
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='bingo_app/password_reset_complete.html'
    ), name='password_reset_complete'),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),

    
    
    # App de Bingo
    path('', include('bingo_app.urls')),
    
    # API (opcional para futura expansión)
   # path('api/', include('bingo_app.api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom admin site headers
admin.site.site_header = "Administración de Bingo Online"
admin.site.site_title = "Panel de Administración"
admin.site.index_title = "Bienvenido al sistema de Bingo"