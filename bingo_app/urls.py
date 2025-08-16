from django.urls import include, path
from . import views


urlpatterns = [
    # Vistas principales
    path('lobby/', views.lobby, name='lobby'),
    path('register/', views.register, name='register'),
    path('profile/', views.profile, name='profile'),
    path('profile/request-credits/', views.request_credits, name='request_credits'),
    path('create-game/', views.create_game, name='create_game'),
    path('game/<int:game_id>/', views.game_room, name='game_room'),
    path('game/<int:game_id>/buy-card/', views.buy_card, name='buy_card'),
    path('start-game/<int:game_id>/', views.start_game, name='start_game'),
   # path('claim-bingo/<int:game_id>/', views.claim_bingo, name='claim_bingo'),
    path('toggle-auto-call/<int:game_id>/', views.toggle_auto_call, name='toggle_auto_call'),
    path('api/messages/', views.message_list_api, name='message_list'),
    path('api/messages/send/', views.send_message_api, name='send_message'),
    path('api/messages/unread_count/', views.unread_count_api, name='unread_count'),
    path('api/messages/read/<int:message_id>/', views.mark_conversation_read_api, name='mark_as_read'),
    path('raffles/', views.raffle_lobby, name='raffle_lobby'),
    path('raffles/create/', views.create_raffle, name='create_raffle'),
    path('raffles/<int:raffle_id>/', views.raffle_detail, name='raffle_detail'),
    path('raffles/<int:raffle_id>/draw/', views.draw_raffle, name='draw_raffle'),
    path('messaging/', views.messaging, name='messaging'),
    path('organizer-dashboard/', views.organizer_dashboard, name='organizer_dashboard'),
    path('withdraw/', views.request_withdrawal, name='request_withdrawal'),
    path('game/<int:game_id>/call-number/', views.call_number, name='call_number'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/mark-as-read/<int:notification_id>/', views.mark_notification_as_read, name='mark_notification_as_read'),
    path('notifications/delete/<int:notification_id>/',  views.delete_notification,  name='delete_notification'),


     path('payment-methods/', include([
        path('', views.payment_methods_list, name='payment_methods_list'),
        path('create/', views.create_payment_method, name='create_payment_method'),
        path('<int:method_id>/edit/', views.edit_payment_method, name='edit_payment_method'),
        path('<int:method_id>/delete/', views.delete_payment_method, name='delete_payment_method'),
        path('<int:method_id>/toggle/', views.toggle_payment_method, name='toggle_payment_method'),
    ])),


    path('credit/', include([
        path('request/', views.request_credits, name='request_credits'),
        #path('history/', views.credit_history, name='credit_history'),
        
        # Panel de administración
        path('admin/', include([
            path('requests/', views.credit_requests_list, name='credit_requests_list'),
            path('requests/<int:request_id>/', views.process_request, name='process_request'),
            path('transactions/', views.transaction_history, name='transaction_history'),
            path('transactions/user/<int:user_id>/', views.transaction_history, name='user_transactions'),
            path('percentages/', views.percentage_settings, name='percentage_settings'),
            path('admin/withdrawals/', views.withdrawal_requests, name='withdrawal_requests'),
            path('admin/withdrawals/all/', views.all_withdrawal_requests, name='all_withdrawal_requests'),
            path('admin/withdrawals/<int:request_id>/', views.process_withdrawal, name='process_withdrawal')
        ])),
    ])),
  
]

# Configuración para manejo de errores
handler404 = 'bingo_app.views.handler404'
handler500 = 'bingo_app.views.handler500'