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
    path('claim-bingo/<int:game_id>/', views.claim_bingo, name='claim_bingo'),
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
    
        ])),
    ])),
  
]

# Configuración para manejo de errores
handler404 = 'bingo_app.views.handler404'
handler500 = 'bingo_app.views.handler500'