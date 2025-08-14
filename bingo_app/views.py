from asyncio.log import logger
from datetime import datetime
from decimal import Decimal
import random
import json

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate
from django.db.models import Sum, Q
from django.db import transaction
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from asgiref.sync import async_to_sync  # Necesario para llamadas síncronas a Channels
from channels.layers import get_channel_layer  # Para enviar mensajes via WebSocket
from .flash_messages import add_flash_message


from .forms import PercentageSettingsForm, RegistrationForm, GameForm, BuyTicketForm, RaffleForm, CreditRequestForm, WithdrawalRequestForm,PaymentMethodForm
from .models import (
    User, Game, Player, ChatMessage, Raffle, Ticket, 
    Transaction, Message, CreditRequest, PercentageSettings, WithdrawalRequest,BankAccount
)

def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('lobby')
        
        else:
            # Agregar mensajes de error detallados
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = RegistrationForm()
    return render(request, 'bingo_app/register.html', {'form': form})

@login_required
def lobby(request):
    active_games = Game.objects.filter(is_active=True, is_finished=False)
    active_raffles = Raffle.objects.filter(status__in=['WAITING', 'IN_PROGRESS'])
    
    if request.user.is_authenticated:
        wins_count = Game.objects.filter(winner=request.user).count()
    else:
        wins_count = 0
    
    context = {
        'games': active_games,
        'raffles': active_raffles,
        'wins_count': wins_count,
    }
    
    return render(request, 'bingo_app/lobby.html', context)

@login_required
def create_game(request):
    if not request.user.is_organizer:
        messages.error(request, "Solo los organizadores pueden crear juegos")
        return redirect('lobby')
    
    if request.method == 'POST':
        form = GameForm(request.POST)
        if form.is_valid():
            # Verificar que el organizador tenga suficiente saldo
            base_prize = form.cleaned_data['base_prize']
            if request.user.credit_balance < base_prize:
                messages.error(request, f'Saldo insuficiente. Necesitas {base_prize} créditos para establecer este premio')
                return render(request, 'bingo_app/create_game.html', {'form': form})
            
            try:
                with transaction.atomic():
                    # Descontar el premio base del saldo del organizador
                    request.user.credit_balance -= base_prize
                    request.user.save()
                    
                    # Crear el juego
                    game = form.save(commit=False)
                    game.organizer = request.user
                    game.current_prize = base_prize  # Establecer premio inicial
                    game.save()

                    # Manejar patrón personalizado
                    if game.winning_pattern == 'CUSTOM':
                        if 'pattern_file' in request.FILES:
                            try:
                                pattern_data = json.load(request.FILES['pattern_file'])
                                game.custom_pattern = pattern_data
                            except json.JSONDecodeError:
                                messages.error(request, "El archivo de patrón debe ser un JSON válido")
                                return render(request, 'bingo_app/create_game.html', {'form': form})
                
                    
                    # Registrar la transacción
                    Transaction.objects.create(
                        user=request.user,
                        amount=-base_prize,
                        transaction_type='PURCHASE',
                        description=f"Premio base para juego {game.name}",
                        related_game=game
                    )
                    
                    messages.success(request, '¡Juego creado exitosamente!')
                    return redirect('game_room', game_id=game.id)
                    
            except Exception as e:
                messages.error(request, f'Error al crear el juego: {str(e)}')
    else:
        form = GameForm()
    
    return render(request, 'bingo_app/create_game.html', {
        'form': form,
        'current_balance': request.user.credit_balance
    })

@login_required
def game_room(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    player, created = Player.objects.get_or_create(user=request.user, game=game)
    percentage_settings = PercentageSettings.objects.first()
    
    # Handle new player joining
    if created:
        # Verificar si el usuario es el organizador del juego
        is_organizer = request.user == game.organizer
        
        if not is_organizer and request.user.credit_balance < game.entry_price:
            messages.error(request, f'Saldo insuficiente. Necesitas {game.entry_price} créditos para unirte')
            return redirect('lobby')
        
        try:
            with transaction.atomic():
                # Solo cobrar entrada si NO es el organizador
                if is_organizer:
                    # Charge entry fee
                    request.user.credit_balance -= game.entry_price
                    request.user.save()
                    
                    # Record transaction
                    Transaction.objects.create(
                        user=request.user,
                        amount=-game.entry_price,
                        transaction_type='PURCHASE',
                        description=f"Comision por crear partida: {game.name}",
                        related_game=game
                    )                
        except Exception as e:
            messages.error(request, f'Error al unirse a la partida: {str(e)}')
            return redirect('lobby')

    # Handle card purchases
    if request.method == 'POST' and 'buy_card' in request.POST and not game.is_started:
        if len(player.cards) >= game.max_cards_per_player:
            messages.error(request, 'Has alcanzado el límite de cartones')
        elif request.user.credit_balance < game.card_price:
            messages.error(request, f'Saldo insuficiente. Necesitas {game.card_price} créditos')
        else:
            try:
                with transaction.atomic():
                    # Generate new card
                    new_card = generate_bingo_card()
                    player.cards.append(new_card)
                    player.save()
                    
                    # Charge for card
                    request.user.credit_balance -= game.card_price
                    request.user.save()
                    
                    # Record transaction
                    Transaction.objects.create(
                        user=request.user,
                        amount=-game.card_price,
                        transaction_type='PURCHASE',
                        description=f"Cartón adicional para {game.name}",
                        related_game=game
                    )
                    
                    # Distribute purchase
                    distribute_purchase(game, game.card_price, percentage_settings)
                    
                    # Update game stats
                    game.total_cards_sold += 1
                    game.save()
                    
                    # Check for progressive prize
                    check_progressive_prize(game)
                    
                    messages.success(request, '¡Cartón comprado exitosamente!')
                    
            except Exception as e:
                messages.error(request, f'Error al comprar cartón: {str(e)}')

    # Handle bingo claims
    if request.method == 'POST' and 'claim_bingo' in request.POST and game.is_started and not game.is_finished:
        if player.check_bingo():
            try:
                with transaction.atomic():
                    # Mark winner
                    player.is_winner = True
                    player.save()
                    
                    game.winner = request.user
                    game.is_finished = True
                    game.save()

                    print("PASE POR AQUI EN CLAIM 1")
                    add_flash_message(request, f"¡GANASTE EL BINGO! Premio: {game.current_prize} créditos")

                    
                    
                     # Enviar notificación via WebSocket

                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f"user_{request.user.id}",  # Grupo único por usuario
                        {
                            'type': 'win_notification',
                            'message': f"¡BINGO! Ganaste {game.current_prize} créditos",
                            'prize': float(game.current_prize)
                        }
                    )
                    
                    # Award prize
                    # if game.current_prize > 0:
                    #     request.user.credit_balance += game.current_prize
                    #     request.user.save()
                        
                    #     Transaction.objects.create(
                    #         user=request.user,
                    #         amount=game.current_prize,
                    #         transaction_type='PRIZE',
                    #         description=f"Premio por ganar {game.name}",
                    #         related_game=game
                    #     )
                        
                    #     print("PASE POR AQUI EN CLAIM 2")
                    #     messages.success(request, f'¡BINGO! Has ganado {game.current_prize} créditos')
                    
                    # # Distribute remaining funds
                    # distribute_remaining_funds(game, percentage_settings)

                    
            except Exception as e:
                messages.error(request, f'Error al procesar el premio: {str(e)}')
        else:
            messages.error(request, 'No has completado el patrón ganador')

    chat_messages = ChatMessage.objects.filter(game=game).order_by('-timestamp')[:50]
    
    return render(request, 'bingo_app/game_room.html', {
        'game': game,
        'player': player,
        'chat_messages': chat_messages,
    })

def distribute_purchase(game, amount, percentage_settings):
    """Distribute card purchase according to percentages"""
    admin_share = amount * (percentage_settings.admin_percentage / 100)
    organizer_share = amount * (percentage_settings.organizer_percentage / 100)
    
    # Credit admin
    admin = User.objects.filter(is_admin=True).first()
    if admin:
        admin.credit_balance += admin_share
        admin.save()
        Transaction.objects.create(
            user=admin,
            amount=admin_share,
            transaction_type='ADMIN_ADD',
            description=f"Porcentaje admin de compra en {game.name}",
            related_game=game
        )
    
    # Credit organizer
    game.organizer.credit_balance += organizer_share
    game.organizer.save()
    Transaction.objects.create(
        user=game.organizer,
        amount=organizer_share,
        transaction_type='ADMIN_ADD',
        description=f"Porcentaje organizador de compra en {game.name}",
        related_game=game
    )

def check_progressive_prize(self):
    """Verifica y aplica premios progresivos, devuelve el incremento del premio"""
    old_prize = self.current_prize
    self.current_prize = self.calculate_current_prize()
    self.save()
    
    # Calcula el próximo objetivo si hay premios progresivos
    if self.progressive_prizes:
        next_target = None
        for prize in sorted(self.progressive_prizes, key=lambda x: x['target']):
            if self.total_cards_sold < prize['target']:
                next_target = prize['target']
                break
        
        self.next_prize_target = next_target
        self.save()
    
    return self.current_prize - old_prize  # Devuelve solo el incremento

def distribute_remaining_funds(game, percentage_settings):
    """Distribute remaining funds after game ends"""
    total_collected = game.entry_price * game.player_set.count()
    total_prize = game.current_prize if game.current_prize else 0
    
    remaining_funds = total_collected - total_prize
    
    if remaining_funds > 0:
        admin_share = remaining_funds * (percentage_settings.admin_percentage / 100)
        organizer_share = remaining_funds * (percentage_settings.organizer_percentage / 100)
        
        # Credit admin
        admin = User.objects.filter(is_admin=True).first()
        if admin:
            admin.credit_balance += admin_share
            admin.save()
            Transaction.objects.create(
                user=admin,
                amount=admin_share,
                transaction_type='ADMIN_ADD',
                description=f"Porcentaje admin final de {game.name}",
                related_game=game
            )
        
        # Credit organizer
        game.organizer.credit_balance += organizer_share
        game.organizer.save()
        Transaction.objects.create(
            user=game.organizer,
            amount=organizer_share,
            transaction_type='ADMIN_ADD',
            description=f"Porcentaje organizador final de {game.name}",
            related_game=game
        )


def generate_bingo_card():
    """Genera un cartón de Bingo tradicional 5x5 con letras B-I-N-G-O y comodín central"""
    # Rangos para cada columna según las letras B-I-N-G-O
    ranges = {
        'B': (1, 15),
        'I': (16, 30),
        'N': (31, 45),
        'G': (46, 60),
        'O': (61, 75)
    }
    
    card = []
    for letter in ['B', 'I', 'N', 'G', 'O']:
        # Generar 5 números únicos para cada columna
        start, end = ranges[letter]
        numbers = random.sample(range(start, end+1), 5)
        
        # Para la columna N (tercera columna), el tercer número es comodín (0 o vacío)
        if letter == 'N':
            numbers[2] = 0  # O usar "" para representar el comodín
        
        card.append(numbers)
    
    # Transponer la matriz para tener filas en lugar de columnas
    card_rows = list(zip(*card))
    
    return list(card_rows)

@login_required
def profile(request):
    won_raffles = Raffle.objects.filter(winner=request.user)  # ← Nuevo
    return render(request, 'bingo_app/profile.html', {
        'user': request.user,
        'games_created': request.user.organized_games.all(),
        'games_playing': request.user.player_set.all(),
        'won_games': Game.objects.filter(winner=request.user),
        'won_raffles': won_raffles,  # ← Añadido al contexto

    })

@login_required
def request_credits(request):
    # Obtener SOLO métodos activos ordenados
    payment_methods = BankAccount.objects.filter(is_active=True).order_by('-order', 'title')
    
    if request.method == 'POST':
        form = CreditRequestForm(request.POST, request.FILES)
        if form.is_valid():
            credit_request = form.save(commit=False)
            credit_request.user = request.user
            credit_request.save()
            messages.success(request, '¡Solicitud enviada con éxito!')
            return redirect('profile')
    else:
        form = CreditRequestForm()
    
    return render(request, 'bingo_app/credit_request.html', {
        'form': form,
        'payment_methods': payment_methods  # Asegúrate que este nombre coincida con la plantilla
    })

@staff_member_required
def credit_requests_list(request):
    requests = CreditRequest.objects.filter(status='pending').order_by('created_at')
    return render(request, 'bingo_app/admin/credit_requests.html', {'requests': requests})

@staff_member_required
def process_request(request, request_id):
    credit_request = get_object_or_404(CreditRequest, id=request_id)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            credit_request.status = 'approved'
            credit_request.user.credit_balance += credit_request.amount
            credit_request.user.save()
            messages.success(request, 'Solicitud aprobada y créditos asignados')
        elif action == 'reject':
            credit_request.status = 'rejected'
            messages.success(request, 'Solicitud rechazada')
        credit_request.admin_notes = request.POST.get('notes', '')
        credit_request.processed_at = datetime.now()
        credit_request.save()
        return redirect('credit_requests_list')
    return render(request, 'bingo_app/admin/process_request.html', {'request': credit_request})

@login_required
@require_http_methods(["POST"])
def buy_card(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    player = get_object_or_404(Player, user=request.user, game=game)
    
    # Validaciones iniciales
    if game.is_started:
        return JsonResponse({
            'success': False, 
            'error': 'No se pueden comprar cartones después de que el juego ha comenzado'
        }, status=400)
    
    if len(player.cards) >= game.max_cards_per_player:
        return JsonResponse({
            'success': False, 
            'error': 'Has alcanzado el límite de cartones para esta partida'
        }, status=400)
    
    if request.user.credit_balance < game.card_price:
        return JsonResponse({
            'success': False, 
            'error': f'Saldo insuficiente. Necesitas {game.card_price} créditos'
        }, status=400)
    
    try:
        with transaction.atomic():
            # Generar nuevo cartón
            new_card = generate_bingo_card()
            player.cards.append(new_card)
            player.save()
            
            # Descontar créditos
            request.user.credit_balance -= game.card_price
            request.user.save()
            
            # Registrar transacción
            Transaction.objects.create(
                user=request.user,
                amount=-game.card_price,
                transaction_type='PURCHASE',
                description=f"Compra de cartón para partida: {game.name}",
                related_game=game
            )
            
            # Actualizar estadísticas del juego
            game.total_cards_sold += 1
            game.save()
            
            # Verificar premio progresivo
            prize_increase = game.check_progressive_prize()
            
            response_data = {
                'success': True,
                'new_balance': float(request.user.credit_balance),
                'player_cards_count': len(player.cards),
                'new_card': new_card,  # Enviar el cartón en la respuesta
                'prize_increased': prize_increase > 0,
                'new_prize': float(game.current_prize),
                'increase_amount': float(prize_increase) if prize_increase > 0 else 0,
                'total_cards_sold': game.total_cards_sold,
                'next_prize_target': game.next_prize_target,
                'progress_percentage': game.progress_percentage
            }
            
            # Modificar el mensaje WebSocket para que NO incluya el cartón para el usuario actual
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'game_{game.id}',
                {
                    'type': 'card_purchased',
                    'user': request.user.username,
                    'new_balance': float(request.user.credit_balance),
                    'player_cards_count': len(player.cards),
                    'new_card':  new_card,  # No enviar cartón al propio usuario
                    'prize_increased': prize_increase > 0,
                    'new_prize': float(game.current_prize),
                    'increase_amount': float(prize_increase) if prize_increase > 0 else 0,
                    'total_cards_sold': game.total_cards_sold,
                    'next_prize_target': game.next_prize_target,
                    'progress_percentage': game.progress_percentage
                }
            )
            
            return JsonResponse(response_data)
            
    except Exception as e:
        logger.error(f"Error en compra de cartón: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Error en la transacción: {str(e)}'
        }, status=500)
    
@login_required
@require_http_methods(["POST"])
def start_game(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    
    if request.user != game.organizer:
        return JsonResponse({
            'success': False, 
            'error': 'Solo el organizador puede iniciar el juego'
        })
    
    if game.is_started:
        return JsonResponse({
            'success': False, 
            'error': 'El juego ya ha comenzado'
        })
    
    if game.is_finished:
        return JsonResponse({
            'success': False, 
            'error': 'El juego ya ha terminado'
        })
    
    with transaction.atomic():
        game.refresh_from_db()
        if game.total_cards_sold > game.max_cards_sold:
            game.max_cards_sold = game.total_cards_sold
            game.save()
    
        if game.start_game():
            return JsonResponse({
                'success': True,
                'max_cards_sold': game.max_cards_sold,
                'total_cards_sold': game.total_cards_sold
            })
        else:
            return JsonResponse({
                'success': False, 
                'error': 'No se pudo iniciar el juego'
            })

# @login_required
# @require_http_methods(["POST"])
# def claim_bingo(request, game_id):
#     game = get_object_or_404(Game, id=game_id)
#     player = get_object_or_404(Player, user=request.user, game=game)
    
#     if not game.is_started:
#         return JsonResponse({
#             'success': False, 
#             'error': 'El juego no ha comenzado'
#         })
    
#     if game.is_finished:
#         return JsonResponse({
#             'success': False, 
#             'error': 'El juego ya ha terminado'
#         })
    
#     if player.check_bingo():
#         try:
#             with transaction.atomic():
#                 player.is_winner = True
#                 player.save()

#                 add_flash_message(request, f"¡GANASTE EL BINGO! Premio: {game.current_prize} créditos")


#                 # Notificar al ganador
#                 channel_layer = get_channel_layer()
#                 async_to_sync(channel_layer.group_send)(
#                     f"user_{request.user.id}",
#                     {
#                         'type': 'win_notification',
#                         'message': f"¡BINGO! Ganaste {game.current_prize} créditos",
#                         'prize': float(game.current_prize)  # Corrige este typo a 'prize'
#                     }
#                 )
                    
#                 # Usar el método end_game para distribuir el premio
#                 if game.end_game(request.user):
#                     return JsonResponse({
#                         'success': True,
#                         'message': f'¡BINGO! Has ganado {game.current_prize} créditos',
#                         'prize': float(game.current_prize),
#                         'winner': request.user.username
#                     })
#                 else:
#                     return JsonResponse({
#                         'success': False,
#                         'error': 'Error al distribuir el premio'
#                     }, status=500)
                
#         except Exception as e:
#             return JsonResponse({
#                 'success': False,
#                 'error': f'Error al procesar el premio: {str(e)}'
#             }, status=500)
#     else:
#         return JsonResponse({
#             'success': False, 
#             'error': 'No has completado el patrón ganador'
#         })
    
@login_required
@require_http_methods(["POST"])
def toggle_auto_call(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    
    if request.user != game.organizer:
        return JsonResponse({
            'success': False, 
            'error': 'Solo el organizador puede controlar la llamada automática'
        })
    
    if not game.is_started or game.is_finished:
        return JsonResponse({
            'success': False, 
            'error': 'El juego no está en progreso'
        })
    
    if game.is_auto_calling:
        game.stop_auto_calling()
        return JsonResponse({
            'success': True, 
            'is_auto_calling': False, 
            'message': 'Llamada automática detenida'
        })
    else:
        game.start_auto_calling()
        return JsonResponse({
            'success': True, 
            'is_auto_calling': True, 
            'message': 'Llamada automática iniciada'
        })

@login_required
def message_list_api(request):
    user_id = request.GET.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'user_id parameter is required'}, status=400)
    
    try:
        other_user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    
    messages = Message.objects.filter(
        Q(sender=request.user, recipient=other_user) |
        Q(sender=other_user, recipient=request.user)
    ).order_by('timestamp')
    
    messages_data = [{
        'id': msg.id,
        'sender': {
            'id': msg.sender.id,
            'username': msg.sender.username,
            'is_admin': msg.sender.is_admin,
            'is_organizer': msg.sender.is_organizer
        },
        'content': msg.content,
        'timestamp': msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        'is_read': msg.is_read
    } for msg in messages]
    
    return JsonResponse({'messages': messages_data})

@login_required
@require_http_methods(["POST"])
def send_message_api(request):
    try:
        data = json.loads(request.body)
        recipient = User.objects.get(id=data.get('recipient_id'))
        
        message = Message.objects.create(
            sender=request.user,
            recipient=recipient,
            content=data.get('content', '')
        )
        
        return JsonResponse({
            'status': 'success',
            'message': {
                'id': message.id,
                'sender': {
                    'id': message.sender.id,
                    'username': message.sender.username,
                    'is_admin': message.sender.is_admin,
                    'is_organizer': message.sender.is_organizer
                },
                'content': message.content,
                'timestamp': message.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
        }, status=201)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@login_required
def unread_count_api(request):
    total_unread = Message.objects.filter(
        recipient=request.user,
        is_read=False
    ).count()
    
    return JsonResponse({'unread_count': total_unread})

@login_required
@require_http_methods(["POST"])
def mark_conversation_read_api(request):
    user_id = request.GET.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'user_id parameter is required'}, status=400)
    
    try:
        sender = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    
    Message.objects.filter(
        sender=sender,
        recipient=request.user,
        is_read=False
    ).update(is_read=True)
    
    return JsonResponse({'status': 'success'})

@login_required
def messaging(request):
    # Get all users except current user
    all_users = User.objects.exclude(id=request.user.id)
    
    # Get existing conversations
    user_messages = Message.objects.filter(
        Q(sender=request.user) | Q(recipient=request.user)
    )
    
    # Get user IDs with conversations
    user_ids_with_chats = set()
    for message in user_messages:
        if message.sender != request.user:
            user_ids_with_chats.add(message.sender.id)
        if message.recipient != request.user:
            user_ids_with_chats.add(message.recipient.id)
    
    # Separate users with and without conversations
    users_with_chats = User.objects.filter(id__in=user_ids_with_chats)
    users_without_chats = all_users.exclude(id__in=user_ids_with_chats)
    
    # Prepare conversation data
    conversations = []
    for user in users_with_chats:
        last_message = Message.objects.filter(
            Q(sender=request.user, recipient=user) |
            Q(sender=user, recipient=request.user)
        ).order_by('-timestamp').first()
        
        unread_count = Message.objects.filter(
            sender=user,
            recipient=request.user,
            is_read=False
        ).count()
        
        conversations.append({
            'other_user': user,
            'last_message': last_message.content if last_message else '',
            'unread_count': unread_count,
            'last_message_time': last_message.timestamp if last_message else None
        })
    
    # Sort conversations by last message
    conversations.sort(key=lambda x: x['last_message_time'] or datetime.min, reverse=True)
    
    return render(request, 'bingo_app/messaging.html', {
        'conversations': conversations,
        'users_without_chats': users_without_chats
    })

@login_required
def organizer_dashboard(request):
    if not (request.user.is_organizer or request.user.is_admin):
        return redirect('lobby')
    
    # Estadísticas principales
    total_players = User.objects.filter(is_organizer=False).count()
    total_games = Game.objects.filter(organizer=request.user).count()
    total_raffles = Raffle.objects.filter(organizer=request.user).count()
    
    # Lista paginada de todos los jugadores
    player_list = User.objects.filter(is_organizer=False).order_by('-credit_balance')
    paginator = Paginator(player_list, 25)  # 25 jugadores por página
    page_number = request.GET.get('page')
    all_players = paginator.get_page(page_number)
    
    # Mensajes recientes
    recent_messages = Message.objects.filter(
        recipient=request.user
    ).order_by('-timestamp')[:5]
    
    # Calcular estadísticas de saldos
    balance_stats = {
        'total_balance': sum(p.credit_balance for p in player_list),
        'average_balance': sum(p.credit_balance for p in player_list) / total_players if total_players > 0 else 0,
        'players_with_balance': User.objects.filter(is_organizer=False, credit_balance__gt=0).count(),
        'players_zero_balance': User.objects.filter(is_organizer=False, credit_balance=0).count(),
        'players_negative_balance': User.objects.filter(is_organizer=False, credit_balance__lt=0).count(),
    }
    
    # Juegos recientes del organizador
    recent_games = Game.objects.filter(organizer=request.user).order_by('-created_at')[:3]
    
    context = {
        'total_players': total_players,
        'total_games': total_games,
        'total_raffles': total_raffles,
        'all_players': all_players,
        'recent_messages': recent_messages,
        'balance_stats': balance_stats,
        'recent_games': recent_games,
    }
    
    return render(request, 'bingo_app/organizer_dashboard.html', context)

@login_required
def create_raffle(request):
    if not request.user.is_organizer:
        return redirect('lobby')
    
    if request.method == 'POST':
        form = RaffleForm(request.POST)
        if form.is_valid():
            raffle = form.save(commit=False)
            
            # Verificar que el organizador tenga suficiente saldo para el premio
            if request.user.credit_balance < raffle.prize:
                messages.error(request, f'Saldo insuficiente. Necesitas {raffle.prize} créditos para establecer este premio')
                return render(request, 'bingo_app/create_raffle.html', {'form': form})
            
            try:
                with transaction.atomic():
                    # Descontar el premio del saldo del organizador
                    request.user.credit_balance -= raffle.prize
                    request.user.save()
                    
                    # Crear la rifa
                    raffle.organizer = request.user
                    raffle.save()
                    
                    # Registrar la transacción
                    Transaction.objects.create(
                        user=request.user,
                        amount=-raffle.prize,
                        transaction_type='PURCHASE',
                        description=f"Premio para rifa {raffle.title}",
                        related_game=None
                    )
                    
                    messages.success(request, '¡Rifa creada exitosamente!')
                    return redirect('raffle_detail', raffle_id=raffle.id)
                    
            except Exception as e:
                messages.error(request, f'Error al crear la rifa: {str(e)}')
    else:
        form = RaffleForm()
    
    return render(request, 'bingo_app/create_raffle.html', {
        'form': form,
        'current_balance': request.user.credit_balance
    })

@login_required
def raffle_lobby(request):
    active_raffles = Raffle.objects.filter(status__in=['WAITING', 'IN_PROGRESS'])
    finished_raffles = Raffle.objects.filter(status='FINISHED')[:5]
    
    return render(request, 'bingo_app/raffle_lobby.html', {
        'active_raffles': active_raffles,
        'finished_raffles': finished_raffles,
    })

@login_required
def raffle_detail(request, raffle_id):
    raffle = get_object_or_404(Raffle, id=raffle_id)
    percentage_settings = PercentageSettings.objects.first()
    
    # Preparar datos de tickets
    tickets_dict = {t.number: t for t in raffle.tickets.select_related('owner')}
    user_tickets = raffle.tickets.filter(owner=request.user)
    available_numbers = list(range(raffle.start_number, raffle.end_number + 1))
    sold_numbers = list(tickets_dict.keys())
    
    # Manejar compra de tickets
    if request.method == 'POST' and raffle.status == 'WAITING':
        form = BuyTicketForm(request.POST)
        if form.is_valid():
            number = form.cleaned_data['number']
            
            if number not in available_numbers:
                messages.error(request, 'Número fuera de rango')
            elif number in sold_numbers:
                messages.error(request, 'Este número ya está comprado')
            elif request.user.credit_balance < raffle.ticket_price:
                messages.error(request, 'Saldo insuficiente')
            else:
                try:
                    with transaction.atomic():
                        # Crear ticket
                        Ticket.objects.create(
                            raffle=raffle,
                            number=number,
                            owner=request.user
                        )
                        
                        # Descontar créditos
                        request.user.credit_balance -= raffle.ticket_price
                        request.user.save()
                        
                        # Registrar transacción
                        Transaction.objects.create(
                            user=request.user,
                            amount=-raffle.ticket_price,
                            transaction_type='PURCHASE',
                            description=f"Ticket #{number} para rifa: {raffle.title}",
                            related_game=None
                        )
                        
                        
                        messages.success(request, f'¡Has comprado el ticket #{number}!')
                        
                        # Verificar si la rifa debe cambiar de estado
                        check_raffle_progress(raffle)
                        
                except Exception as e:
                    messages.error(request, f'Error al comprar ticket: {str(e)}')
            
            return redirect('raffle_detail', raffle_id=raffle.id)
    else:
        form = BuyTicketForm()
    
    # Handle raffle draw (organizer only)
    if request.method == 'POST' and 'draw_raffle' in request.POST and request.user == raffle.organizer:
        if raffle.status != 'WAITING' and raffle.status != 'IN_PROGRESS':
            messages.error(request, 'Esta rifa ya ha sido sorteada')
        elif not tickets_dict:
            messages.error(request, 'No hay tickets vendidos para sortear')
        else:
            try:
                with transaction.atomic():
                    # Seleccionar ganador aleatorio
                    winning_number = random.choice(list(tickets_dict.keys()))
                    winner = tickets_dict[winning_number].owner
                    
                    # Actualizar rifa
                    raffle.winning_number = winning_number
                    raffle.winner = winner
                    raffle.status = 'FINISHED'
                    raffle.save()
                    
                    # Premiar al ganador
                    winner.credit_balance += raffle.prize
                    winner.save()

                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f"user_{winner.id}",
                        {
                            'type': 'win_notification',
                            'message': f"¡Felicidades! Ganaste {raffle.title}",
                        }
                    )

                    
                    Transaction.objects.create(
                        user=winner,
                        amount=raffle.prize,
                        transaction_type='PRIZE',
                        description=f"Premio de rifa: {raffle.title}",
                        related_game=None
                    )
                    
                    
                    messages.success(request, f'¡El ganador es {winner.username} con el ticket #{winning_number}!')
                    
            except Exception as e:
                messages.error(request, f'Error al realizar el sorteo: {str(e)}')
            
            return redirect('raffle_detail', raffle_id=raffle.id)
    
    return render(request, 'bingo_app/raffle_detail.html', {
        'raffle': raffle,
        'user_tickets': user_tickets,
        'available_numbers': available_numbers,
        'sold_numbers': sold_numbers,
        'tickets_dict': tickets_dict,
        'form': form,
        'progress_percentage': (len(sold_numbers) / raffle.total_tickets) * 100,
    })

@staff_member_required
def percentage_settings(request):
    settings, created = PercentageSettings.objects.get_or_create(pk=1)
    
    if request.method == 'POST':
        form = PercentageSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            settings = form.save(commit=False)
            settings.updated_by = request.user
            settings.save()
            messages.success(request, 'Porcentajes actualizados correctamente')
            return redirect('percentage_settings')
    else:
        form = PercentageSettingsForm(instance=settings)
    
    return render(request, 'bingo_app/admin/percentage_settings.html', {
        'form': form,
        'settings': settings
    })

@staff_member_required
def transaction_history(request, user_id=None):
    transactions = Transaction.objects.all()
    
    if user_id:
        transactions = transactions.filter(user__id=user_id)
    
    return render(request, 'bingo_app/admin/transaction_history.html', {
        'transactions': transactions.order_by('-created_at')
    })



def check_raffle_progress(raffle):
    """Verifica el progreso de la rifa y actualiza el estado si es necesario"""
    sold_count = raffle.tickets.count()
    if sold_count >= raffle.total_tickets * 0.5 and raffle.status == 'WAITING':
        raffle.status = 'IN_PROGRESS'
        raffle.save()


@login_required
def draw_raffle(request, raffle_id):
    raffle = get_object_or_404(Raffle, id=raffle_id)
    percentage_settings = PercentageSettings.objects.first()
    
    # Validaciones
    if request.user != raffle.organizer:
        messages.error(request, "Solo el organizador puede sortear")
        return redirect('raffle_detail', raffle_id=raffle.id)
    
    if raffle.status == 'FINISHED':
        messages.error(request, "Esta rifa ya terminó")
        return redirect('raffle_detail', raffle_id=raffle.id)
    
    if not raffle.tickets.exists():
        messages.error(request, "No hay tickets vendidos")
        return redirect('raffle_detail', raffle_id=raffle.id)
    
     # Verificar si hay un número ganador manual definido
    if raffle.is_manual_winner and raffle.manual_winning_number:
        try:
            winning_ticket = raffle.tickets.get(number=raffle.manual_winning_number)
        except Ticket.DoesNotExist:
            messages.error(request, f"El número ganador manual #{raffle.manual_winning_number} no fue vendido")
            return redirect('raffle_detail', raffle_id=raffle.id)
    else:
        # Selección aleatoria normal
        winning_ticket = random.choice(raffle.tickets.all())
    
    try:
        with transaction.atomic():
            # 1. Seleccionar ganador con select_for_update para bloquear el registro
            winning_ticket = Ticket.objects.select_related('owner').select_for_update().get(
                id=random.choice([t.id for t in raffle.tickets.all()])
            )
            winner = winning_ticket.owner
            
            # 2. Calcular valores
            total_tickets_income = raffle.ticket_price * raffle.tickets.count()
            player_percent = percentage_settings.player_percentage / 100
            player_prize = raffle.prize * Decimal(player_percent)
            
            # 3. Actualizar saldo del ganador de forma segura
            winner.refresh_from_db()  # Asegurarnos de tener los datos más recientes
            winner.credit_balance += player_prize
            winner.save()

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{winner.id}",
                {
                    'type': 'win_notification',
                    'message': f"¡Felicidades! Ganaste {raffle.title}",
                }
            )

            
            # Registrar transacción
            Transaction.objects.create(
                user=winner,
                amount=player_prize,
                transaction_type='PRIZE',
                description=f"Premio de {raffle.title} ({percentage_settings.player_percentage}% de {raffle.prize})",
                related_game=None
            )
            
            # 4. Distribución al organizador
            organizer_percent = percentage_settings.organizer_percentage / 100
            organizer_prize_portion = raffle.prize * Decimal(organizer_percent)
            organizer_total = organizer_prize_portion + total_tickets_income
            
            raffle.organizer.refresh_from_db()
            raffle.organizer.credit_balance += organizer_total
            raffle.organizer.save()
            
            Transaction.objects.create(
                user=raffle.organizer,
                amount=organizer_total,
                transaction_type='RAFFLE_INCOME',
                description=f"Ingresos de {raffle.title} ({percentage_settings.organizer_percentage}% premio + tickets)",
                related_game=None
            )
            
            # 5. Distribución al admin
            admin = User.objects.filter(is_admin=True).first()
            if admin:
                admin_percent = percentage_settings.admin_percentage / 100
                admin_prize = raffle.prize * Decimal(admin_percent)
                
                admin.refresh_from_db()
                admin.credit_balance += admin_prize
                admin.save()
                
                Transaction.objects.create(
                    user=admin,
                    amount=admin_prize,
                    transaction_type='ADMIN_ADD',
                    description=f"Porcentaje de {raffle.title}",
                    related_game=None
                )
            
            # 6. Actualizar rifa
            raffle.winning_number = winning_ticket.number
            raffle.winner = winner
            raffle.status = 'FINISHED'
            raffle.final_prize = player_prize
            raffle.tickets_income = total_tickets_income
            raffle.save()

            request.session['show_win_notification'] = {
                    'message': f"¡GANASTE LA RIFA! Premio: {raffle.prize} créditos",
                    'prize': float(raffle.prize),
                    'game': raffle.title
                }
            messages.success(request, 
                f'¡Sorteo completado! Ganador: {winner.username} '
                f'con ticket #{winning_ticket.number}. '
                f'Premio: {player_prize} créditos. '
                f'Saldo actual del ganador: {winner.credit_balance} créditos.'
            )

            return redirect('raffle_detail', raffle_id=raffle.id)
            
    except Exception as e:
        messages.error(request, f'Error en sorteo: {str(e)}')
        logger.error(f"Error en draw_raffle: {str(e)}", exc_info=True)
    
    return redirect('raffle_detail', raffle_id=raffle.id)


@login_required
def request_withdrawal(request):
    if request.method == 'POST':
        form = WithdrawalRequestForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            
            # Verificar que el usuario tenga suficiente saldo
            if request.user.credit_balance < amount:
                messages.error(request, 'Saldo insuficiente para este retiro')
                return render(request, 'bingo_app/request_withdrawal.html', {'form': form})
            
            try:
                with transaction.atomic():
                    # Crear la solicitud de retiro
                    withdrawal = form.save(commit=False)
                    withdrawal.user = request.user
                    withdrawal.status = 'PENDING'
                    withdrawal.save()
                    
                    # Descontar los créditos del usuario
                    request.user.credit_balance -= amount
                    request.user.save()
                    
                    # Registrar la transacción
                    Transaction.objects.create(
                        user=request.user,
                        amount=-amount,
                        transaction_type='WITHDRAWAL',
                        description=f"Solicitud de retiro #{withdrawal.id}",
                        related_game=None
                    )
                    
                    messages.success(request, 'Solicitud de retiro enviada. Los créditos han sido reservados.')
                    return redirect('profile')
                    
            except Exception as e:
                messages.error(request, f'Error al procesar la solicitud: {str(e)}')
    else:
        form = WithdrawalRequestForm(initial={
            'account_holder_name': request.user.get_full_name() or request.user.username
        })
    
    return render(request, 'bingo_app/request_withdrawal.html', {
        'form': form,
        'current_balance': request.user.credit_balance
    })

@staff_member_required
def withdrawal_requests(request):
    requests = WithdrawalRequest.objects.filter(status='PENDING').order_by('created_at')
    return render(request, 'bingo_app/admin/withdrawal_requests.html', {
        'requests': requests,
        'section': 'pending'
    })

@staff_member_required
def all_withdrawal_requests(request):
    requests = WithdrawalRequest.objects.all().order_by('-created_at')
    status_filter = request.GET.get('status')
    
    if status_filter:
        requests = requests.filter(status=status_filter)
    
    return render(request, 'bingo_app/admin/withdrawal_requests.html', {
        'requests': requests,
        'section': 'all'
    })

@staff_member_required
def process_withdrawal(request, request_id):
    withdrawal = get_object_or_404(WithdrawalRequest, id=request_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        notes = request.POST.get('admin_notes', '')
        
        try:
            with transaction.atomic():
                if action == 'approve':
                    # Marcar como aprobado (el admin debe hacer la transferencia manualmente)
                    withdrawal.status = 'APPROVED'
                    withdrawal.admin_notes = notes
                    withdrawal.save()
                    
                    messages.success(request, 'Retiro aprobado. Ahora puedes proceder con la transferencia bancaria.')
                
                elif action == 'complete':
                    # Marcar como completado (después de hacer la transferencia)
                    withdrawal.status = 'COMPLETED'
                    withdrawal.transaction_reference = request.POST.get('transaction_reference', '')
                    withdrawal.admin_notes = notes
                    withdrawal.save()
                    
                    messages.success(request, 'Retiro marcado como completado.')
                
                elif action == 'reject':
                    # Rechazar y devolver los créditos al usuario
                    withdrawal.status = 'REJECTED'
                    withdrawal.admin_notes = notes
                    withdrawal.save()
                    
                    # Devolver los créditos al usuario
                    withdrawal.user.credit_balance += withdrawal.amount
                    withdrawal.user.save()
                    
                    # Registrar la transacción de devolución
                    Transaction.objects.create(
                        user=withdrawal.user,
                        amount=withdrawal.amount,
                        transaction_type='WITHDRAWAL_REFUND',
                        description=f"Reembolso de retiro rechazado #{withdrawal.id}",
                        related_game=None
                    )
                    
                    messages.success(request, 'Retiro rechazado y créditos devueltos al usuario.')
                
                return redirect('withdrawal_requests')
                
        except Exception as e:
            messages.error(request, f'Error al procesar la solicitud: {str(e)}')
    
    return render(request, 'bingo_app/admin/process_withdrawal.html', {
        'withdrawal': withdrawal
    })

@login_required
@require_http_methods(["POST"])
def call_number(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    
    if request.user != game.organizer:
        return JsonResponse({'success': False, 'error': 'Solo el organizador puede llamar números'}, status=403)
    
    try:
        data = json.loads(request.body)
        number = int(data['number'])
        
        if number < 1 or number > 90:
            return JsonResponse({'success': False, 'error': 'Número fuera de rango'}, status=400)
            
        if number in game.called_numbers:
            return JsonResponse({'success': False, 'error': 'Número ya llamado'}, status=400)
            
        game.current_number = number
        game.called_numbers.append(number)
        game.save()
        
        # Verificar si algún jugador ha ganado
        winner = None
        for player in game.player_set.all():
            if player.check_bingo():
                winner = player.user
                break
        
        # Notificar via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{game.id}',
            {
                'type': 'number_called',
                'number': number,
                'called_numbers': game.called_numbers,
                'is_manual': True,
                'has_winner': winner is not None,
                'winner': winner.username if winner else None
            }
        )
        
        # Si hay ganador, finalizar el juego (esto activará la distribución de premios)
        if winner:
            print(f"Ganador detectado: {winner.username}")
            try:
                with transaction.atomic():
                    success = game.end_game_manual(winner)
                    if not success:
                        print("Error en end_game")
                        return JsonResponse({
                            'success': False, 
                            'error': 'Error al distribuir premios',
                            'has_winner': True
                        }, status=500)
                    
                    # Verificar distribución
                    winner.refresh_from_db()
                    print(f"Nuevo balance del ganador: {winner.credit_balance}")
                    
                    return JsonResponse({
                        'success': True,
                        'has_winner': True,
                        'winner': winner.username,
                        'new_balance': float(winner.credit_balance)
                    })
            except Exception as e:
                print(f"Error en transacción: {str(e)}")
                return JsonResponse({
                    'success': False,
                    'error': str(e),
                    'has_winner': True
                }, status=500)
        
        return JsonResponse({
            'success': True, 
            'has_winner': False
        })
        
    except Exception as e:
        print(f"Error general: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    

@staff_member_required
def payment_methods_list(request):
    methods = BankAccount.objects.all().order_by('-order', 'title')
    return render(request, 'bingo_app/admin/payment_methods/list.html', {
        'payment_methods': methods
    })

@staff_member_required
def create_payment_method(request):
    if request.method == 'POST':
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Método de pago creado exitosamente')
            return redirect('payment_methods_list')
    else:
        form = PaymentMethodForm()
    
    return render(request, 'bingo_app/admin/payment_methods/create.html', {
        'form': form,
        'title': 'Crear nuevo método de pago'
    })

@staff_member_required
def edit_payment_method(request, method_id):
    method = get_object_or_404(BankAccount, id=method_id)
    
    if request.method == 'POST':
        form = PaymentMethodForm(request.POST, instance=method)
        if form.is_valid():
            form.save()
            messages.success(request, 'Método de pago actualizado')
            return redirect('payment_methods_list')
    else:
        form = PaymentMethodForm(instance=method)
    
    return render(request, 'bingo_app/admin/payment_methods/create.html', {
        'form': form,
        'title': f'Editar {method.title}'
    })

@staff_member_required
def delete_payment_method(request, method_id):
    method = get_object_or_404(BankAccount, id=method_id)
    if request.method == 'POST':
        method.delete()
        messages.success(request, 'Método de pago eliminado')
    return redirect('payment_methods_list')

@staff_member_required
def toggle_payment_method(request, method_id):
    method = get_object_or_404(BankAccount, id=method_id)
    method.is_active = not method.is_active
    method.save()
    messages.success(request, f'Método {"activado" if method.is_active else "desactivado"}')
    return redirect('payment_methods_list')