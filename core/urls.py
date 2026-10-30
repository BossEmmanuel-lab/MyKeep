from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path
from django.views.static import serve

from app import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', views.health_view, name='health'),
    path('login/', views.login_view, name='login'),
    path('login/2fa/', views.login_2fa_view, name='login_2fa'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_view, name='dashboard'),
    path('subscriptions/', views.subscriptions_view, name='subscriptions'),
    path('subscriptions/save/', views.subscription_save_view, name='subscription_save'),
    path('subscriptions/bulk/', views.subscriptions_bulk_action_view, name='subscriptions_bulk_action'),
    path('subscriptions/<int:sub_id>/renew/', views.subscription_renew_view, name='subscription_renew'),
    path('subscriptions/<int:sub_id>/clone/', views.subscription_clone_view, name='subscription_clone'),
    path('subscriptions/<int:sub_id>/delete/', views.subscription_delete_view, name='subscription_delete'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/save/', views.wishlist_save_view, name='wishlist_save'),
    path('wishlist/<int:item_id>/toggle-purchased/', views.wishlist_toggle_purchased_view, name='wishlist_toggle_purchased'),
    path('wishlist/<int:item_id>/delete/', views.wishlist_delete_view, name='wishlist_delete'),
    path('calendar/', views.calendar_view, name='calendar'),
    path('calendar/ical/', views.export_ical_view, name='export_ical'),
    path('stats/', views.stats_view, name='stats'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/general/', views.settings_save_general_view, name='settings_save_general'),
    path('settings/totp/', views.settings_totp_setup_view, name='settings_totp_setup'),
    path('settings/password/', views.settings_change_password_view, name='settings_change_password'),
    path('settings/theme-toggle/', views.theme_toggle_view, name='theme_toggle'),
    path('settings/taxonomy/', views.settings_manage_taxonomy_view, name='settings_manage_taxonomy'),
    path('settings/notifications/', views.settings_save_notifications_view, name='settings_save_notifications'),
    path('settings/export/', views.export_data_view, name='export_data'),
    path('settings/import/', views.import_data_view, name='import_data'),
    path('api/summary/', views.api_summary_view, name='api_summary'),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
