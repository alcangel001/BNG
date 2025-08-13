from decimal import Decimal
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
import asyncio
from datetime import datetime
from .models import Game, Player, ChatMessage, Transaction, Message, User
from django.db.models import Sum


class BingoConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auto_call_task = None
        self.game = None
        self.game_id = None
        self.game_group_name = None
        self.user = None

    async def connect(self):
        self.user = self.scope.get('user', AnonymousUser())
        self.game_id = self.scope['url_route']['kwargs']['game_id']
        self.game_group_name = f'game_{self.game_id}'
        
        if isinstance(self.user, AnonymousUser):
            await self.close()
            return
        
        self.game = await self.get_game()
        if not self.game:
            await self.close()
            return
            
        await self.channel_layer.group_add(
            self.game_group_name,
            self.channel_name
        )
        await self.accept()

        # Enviar estado actual del juego al conectar
        await self.send_game_status()

    async def send_game_status(self):
        """Envía el estado actual del juego al cliente"""
        game_data = await self.get_game_data()

        await self.send(text_data=json.dumps({
            'type': 'game_status',
            'is_started': game_data['is_started'],
            'is_finished': game_data['is_finished'],
            'is_auto_calling': game_data['is_auto_calling'],
            'current_number': game_data['current_number'],
            'called_numbers': game_data['called_numbers'],
            'current_prize': float(game_data['current_prize']) if isinstance(game_data['current_prize'], Decimal) else game_data['current_prize'],
            'total_cards_sold': game_data['total_cards_sold'],
            'next_prize_target': game_data['next_prize_target'],
            'progress_percentage': game_data['progress_percentage']
        }))

    @database_sync_to_async
    def get_game_data(self):
        if not self.game:
            return None
            
        self.game.refresh_from_db()
        return {
            'is_started': self.game.is_started,
            'is_finished': self.game.is_finished,
            'is_auto_calling': self.game.is_auto_calling,
            'current_number': self.game.current_number,
            'called_numbers': self.game.called_numbers,
            'current_prize': self.game.current_prize,
            'total_cards_sold': self.game.total_cards_sold,
            'next_prize_target': self.game.next_prize_target,
            'progress_percentage': self.game.progress_percentage
        }

    async def disconnect(self, close_code):
        if self.auto_call_task and not self.auto_call_task.done():
            self.auto_call_task.cancel()
            try:
                await self.auto_call_task
            except asyncio.CancelledError:
                pass
        
        if hasattr(self, 'game_group_name'):
            await self.channel_layer.group_discard(
                self.game_group_name,
                self.channel_name
            )

    @database_sync_to_async
    def get_game(self):
        try:
            return Game.objects.get(id=self.game_id)
        except Game.DoesNotExist:
            return None

    @database_sync_to_async
    def start_game(self):
        if not self.game or self.game.is_started or self.game.is_finished:
            return False
            
        self.game.is_started = True
        self.game.save()
        return True

    @database_sync_to_async
    def call_next_number(self):
        if not self.game:
            return None
        return self.game.call_number()

    @database_sync_to_async
    def get_current_numbers(self):
        if not self.game:
            return []
        return self.game.called_numbers

    @database_sync_to_async
    def check_all_players_for_bingo(self):
        if not self.game:
            return None
            
        players = Player.objects.filter(game=self.game).select_related('user')
        for player in players:
            if player.check_bingo():
                return player
        return None

    @database_sync_to_async
    def process_winner(self, player):
        if not self.game or not player:
            return 0.0
            
        self.game.refresh_from_db()
        prize = float(self.game.current_prize) if self.game.current_prize else 0.0
        
        self.game.end_game(player.user)
        return prize

    @database_sync_to_async
    def toggle_auto_call_mode(self):
        if not self.game:
            return False
            
        if self.game.is_auto_calling:
            self.game.stop_auto_calling()
            return False
        else:
            self.game.start_auto_calling()
            return True

    async def start_auto_call_task(self):
        """Inicia la tarea de llamada automática"""
        if self.auto_call_task and not self.auto_call_task.done():
            self.auto_call_task.cancel()
        
        self.auto_call_task = asyncio.create_task(self.auto_call_numbers())

    async def auto_call_numbers(self):
        """Tarea asíncrona para llamada automática de números"""
        while await self.is_auto_calling_active():
            try:
                number = await self.call_next_number()
                if not number:
                    await asyncio.sleep(1)
                    continue

                called_numbers = await self.get_current_numbers()
                winner = await self.check_all_players_for_bingo()

                if winner:
                    prize = await self.process_winner(winner)
                    await self.notify_game_ended(winner.user.username, prize, called_numbers)
                    break

                await self.notify_number_called(number, called_numbers)
                await asyncio.sleep(self.game.auto_call_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error en auto_call_numbers: {str(e)}")
                break

    @database_sync_to_async
    def is_auto_calling_active(self):
        if not self.game:
            return False
        return self.game.is_auto_calling and self.game.is_started and not self.game.is_finished

    async def notify_number_called(self, number, called_numbers):
        await self.channel_layer.group_send(
            self.game_group_name,
            {
                'type': 'number_called',
                'number': number,
                'called_numbers': called_numbers
            }
        )

    async def notify_game_ended(self, winner, prize, called_numbers):
        await self.channel_layer.group_send(
            self.game_group_name,
            {
                'type': 'game_ended',
                'winner': winner,
                'prize': float(prize) if isinstance(prize, Decimal) else prize,
                'called_numbers': called_numbers
            }
        )

    async def notify_game_started(self):
        await self.channel_layer.group_send(
            self.game_group_name,
            {
                'type': 'game_started',
                'is_started': True,
                'is_auto_calling': self.game.is_auto_calling
            }
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            
            if data['type'] == 'start_game':
                if await database_sync_to_async(lambda: self.user == self.game.organizer)():
                    if await self.start_game():
                        await self.notify_game_started()
                        await database_sync_to_async(self.game.start_auto_calling)()
                        await self.start_auto_call_task()
                        await self.channel_layer.group_send(
                            self.game_group_name,
                            {
                                'type': 'auto_call_toggled',
                                'is_auto_calling': True
                            }
                        )

            elif data['type'] == 'toggle_auto_call':
                if await database_sync_to_async(lambda: self.user == self.game.organizer)():
                    is_auto_calling = await self.toggle_auto_call_mode()
                    await self.channel_layer.group_send(
                        self.game_group_name,
                        {
                            'type': 'auto_call_toggled',
                            'is_auto_calling': is_auto_calling
                        }
                    )
                    if is_auto_calling:
                        await self.start_auto_call_task()

            elif data['type'] == 'chat_message':
                message = data.get('message', '').strip()
                if message:
                    await self.handle_chat_message(message)

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            print(f"Error in receive: {str(e)}")

    async def handle_chat_message(self, message):
        await database_sync_to_async(ChatMessage.objects.create)(
            game=self.game,
            user=self.user,
            message=message
        )
        
        await self.channel_layer.group_send(
            self.game_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'user': self.user.username,
                'timestamp': datetime.now().isoformat()
            }
        )

    # Handlers para mensajes recibidos del grupo
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': event['message'],
            'user': event['user'],
            'timestamp': event['timestamp']
        }))

    async def number_called(self, event):
        await self.send(text_data=json.dumps({
            'type': 'number_called',
            'number': event['number'],
            'called_numbers': event['called_numbers']
        }))

    async def game_ended(self, event):
        await self.send(text_data=json.dumps({
            'type': 'game_ended',
            'winner': event['winner'],
            'prize': event['prize'],
            'called_numbers': event['called_numbers']
        }))

    async def auto_call_toggled(self, event):
        await self.send(text_data=json.dumps({
            'type': 'auto_call_toggled',
            'is_auto_calling': event['is_auto_calling']
        }))

    async def game_started(self, event):
        game = await self.get_game()
        await self.send(text_data=json.dumps({
            'type': 'game_started',
            'is_started': event['is_started'],
            'total_cards_sold': game.total_cards_sold,  # Añade esto
             'max_cards_sold': game.max_cards_sold,  # Asegúrate de enviar esto

        }))

    async def game_status(self, event):
        await self.send(text_data=json.dumps(event))

    async def prize_updated(self, event):
        await self.send(text_data=json.dumps({
            'type': 'prize_updated',
            'new_prize': event['new_prize'],
            'increase_amount': event['increase_amount'],
            'total_cards': event['total_cards'],
            'next_target': event['next_target'],
            'progress_percentage': event.get('progress_percentage', 0)
        }))

    async def card_purchased(self, event):
        await self.send(text_data=json.dumps({
            'type': 'card_purchased',
            'user': event['user'],
            'new_balance': event['new_balance'],
            'player_cards_count': event['player_cards_count'],
             'new_card': event['new_card'],
            'prize_increased': event['prize_increased'],
            'new_prize': event['new_prize'],
            'increase_amount': event['increase_amount'],
            'total_cards_sold': event['total_cards_sold'],
            'next_prize_target': event['next_prize_target'],
            'progress_percentage': event.get('progress_percentage', 0)
        }))


class MessageConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.user_group = None

    async def connect(self):
        self.user = self.scope.get('user')
        if isinstance(self.user, AnonymousUser):
            await self.close()
            return
            
        self.user_group = f'user_{self.user.id}'
        
        await self.channel_layer.group_add(
            self.user_group,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'user_group'):
            await self.channel_layer.group_discard(
                self.user_group,
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            
            if data['type'] == 'private_message':
                recipient_id = data.get('recipient_id')
                content = data.get('content', '').strip()
                
                if not recipient_id or not content:
                    return
                    
                try:
                    message = await self.create_message(recipient_id, content)
                    
                    # Enviar al remitente (confirmación)
                    await self.channel_layer.group_send(
                        f'user_{self.user.id}',
                        {
                            'type': 'message_sent',
                            'message': await self.serialize_message(message)
                        }
                    )
                    
                    # Enviar al destinatario
                    await self.channel_layer.group_send(
                        f'user_{recipient_id}',
                        {
                            'type': 'new_message',
                            'message': await self.serialize_message(message)
                        }
                    )
                except User.DoesNotExist:
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Recipient not found'
                    }))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            print(f"Error in receive: {str(e)}")

    @database_sync_to_async
    def create_message(self, recipient_id, content):
        recipient = User.objects.get(id=recipient_id)
        return Message.objects.create(
            sender=self.user,
            recipient=recipient,
            content=content
        )
    
    @database_sync_to_async
    def serialize_message(self, message):
        return {
            'id': message.id,
            'sender': {
                'id': message.sender.id,
                'username': message.sender.username,
                'is_admin': message.sender.is_admin,
                'is_organizer': message.sender.is_organizer
            },
            'recipient': {
                'id': message.recipient.id,
                'username': message.recipient.username
            },
            'content': message.content,
            'timestamp': message.timestamp.isoformat(),
            'is_read': message.is_read
        }

    async def new_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': event['message']
        }))
        
    async def message_sent(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_sent',
            'message': event['message']
        }))
   
class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if isinstance(self.user, AnonymousUser):
            await self.close()
            return
            
        self.user_group = f'user_{self.user.id}'
        await self.channel_layer.group_add(
            self.user_group,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.user_group,
            self.channel_name
        )

    async def win_notification(self, event):
        print(f"DEBUG: Notificación de premio recibida - {event}")  # Esto debería aparecer en los logs del servidor
        await self.send(text_data=json.dumps({
            'type': 'win_notification',
            'message': event['message'],
        }))