from asyncio.log import logger
from datetime import timezone
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser
import random
from asgiref.sync import sync_to_async
from django.core.validators import MinValueValidator, MaxValueValidator
import json
from django.db import transaction
from django.shortcuts import get_object_or_404
from asgiref.sync import async_to_sync  # Necesario para llamadas síncronas a Channels
from channels.layers import get_channel_layer  # Para enviar mensajes via WebSocket


class User(AbstractUser):
    is_organizer = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    credit_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return self.username

class CreditRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('approved', 'Aprobado'),
        ('rejected', 'Rechazado'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    proof = models.FileField(upload_to='credit_proofs/')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    def __str__(self):
        return f"Solicitud de {self.user.username} - ${self.amount}"

class Game(models.Model):
    WINNING_PATTERNS = [
        ('HORIZONTAL', 'Línea horizontal'),
        ('VERTICAL', 'Línea vertical'),
        ('DIAGONAL', 'Línea diagonal (X)'),
        ('FULL', 'Tabla llena'),
        ('CORNERS', 'Cuatro esquinas'),
        ('CUSTOM', 'Patrón personalizado'),

    ]

    custom_pattern = models.JSONField(
        null=True, 
        blank=True,
        help_text="Matriz 5x5 que representa el patrón personalizado (1=casilla requerida, 0=no requerida)"
    )

    # Basic game info
    name = models.CharField(max_length=100)
    organizer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='organized_games')
    password = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Game configuration
    entry_price = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1)],
        verbose_name="Precio de entrada"
    )
    winning_pattern = models.CharField(
        max_length=20, 
        choices=WINNING_PATTERNS, 
        default='FULL'
    )
    card_price = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(0.50)],
        verbose_name="Precio por cartón"
    )
    
    max_cards_per_player = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)]
    )
    
    # Game state
    is_started = models.BooleanField(default=False)
    is_finished = models.BooleanField(default=False)
    winner = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='won_games'
    )
    
    # Called numbers
    current_number = models.IntegerField(null=True, blank=True)
    called_numbers = models.JSONField(default=list)
    
    # Progressive prizes system
    base_prize = models.PositiveIntegerField(
        default=0,
        verbose_name="Premio base"
    )
    progressive_prizes = models.JSONField(
        default=list,
        help_text="[{'target': X, 'prize': Y}, ...]"
    )
    current_prize = models.PositiveIntegerField(
        default=0,
        verbose_name="Premio actual"
    )
    next_prize_target = models.PositiveIntegerField(
        null=True, 
        blank=True,
        verbose_name="Próxima meta"
    )
    total_cards_sold = models.PositiveIntegerField(
        default=0,
        verbose_name="Cartones vendidos"
    )

    max_cards_sold = models.PositiveIntegerField(
        default=0,
        verbose_name="Máximo cartones vendidos"
    )
    
    # Auto-call settings
    auto_call_interval = models.PositiveIntegerField(
        default=5,
        help_text="Intervalo en segundos entre llamadas automáticas"
    )
    is_auto_calling = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    
    @property
    def progress_percentage(self):
        if not self.next_prize_target or self.next_prize_target == 0:
            return 0
        return min(100, (self.total_cards_sold / self.next_prize_target) * 100)
    
    # @property
    # def remaining_cards(self):
    #     if not self.next_prize_target:
    #         return 0
    #     return max(0, self.next_rize_target - self.total_cards_sold)
    
    def call_number(self):
        available_numbers = [n for n in range(1, 91) if n not in self.called_numbers]
        if available_numbers:
            number = random.choice(available_numbers)
            self.current_number = number
            self.called_numbers.append(number)
            self.save()
            return number
        return None
    
    def start_game(self):
        if not self.is_started and not self.is_finished:
            self.is_started = True
            self.save()
            
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'game_{self.id}',
                {
                    'type': 'game_started',
                    'is_started': True,
                    'total_cards_sold': self.total_cards_sold,
                    'max_cards_sold': self.max_cards_sold,  # Asegúrate de enviar esto

                }
            )
            return True
        return False

    def end_game(self, winner):
        if not self.is_finished:
            self.is_finished = True
            self.winner = winner

           
            # Asegurarnos de que el premio actual está calculado correctamente
            self.current_prize = self.calculate_current_prize()

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{winner.id}",
                {
                    'type': 'win_notification',
                    'message': f"¡Felicidades! Ganaste {self.name}",
                }
            )


            print("ACTUALIZACION DEL PRECIO",self.current_prize)
      
            self.save()
            try:
                winning_player = Player.objects.get(user=winner, game=self)
                winning_player.is_winner = True
                winning_player.save()
            except Player.DoesNotExist:
                logger.error(f"No se encontró el jugador ganador para el usuario {winner.username} en el juego {self.name}")
                return False
            

            print("PRIMER SALDO PRUEBAS", winner.credit_balance)
                
            # Distribuir el premio según los porcentajes
            percentage_settings = PercentageSettings.objects.first()
            if percentage_settings and self.current_prize > 0:
                try:
                    with transaction.atomic():

                         # Calcular el total de cartones vendidos
                        
                        total_cards_sold = self.max_cards_sold
                        print("TOTAL CARTONES VENDIDOS",total_cards_sold)
                        total_cards_value = total_cards_sold * self.card_price
                        # Calcular los porcentajes
                        admin_percentage = percentage_settings.admin_percentage / 100
                        organizer_percentage = percentage_settings.organizer_percentage / 100
                        player_percentage = percentage_settings.player_percentage / 100
                        
                        # Distribuir al jugador ganador
                        player_prize = self.current_prize * Decimal(player_percentage)
                        winner.credit_balance += player_prize
                        winner.save()
                        print("DEPURANDO JUGADOR AUMENTO", player_prize)
                        print("DEPURANDO JUGADOR SALDO ACTUAL 1",winner.credit_balance)
                        

                        Transaction.objects.create(
                            user=winner,
                            amount=player_prize,
                            transaction_type='PRIZE',
                            description=f"Premio por ganar {self.name} (parte jugador)",
                            related_game=self
                        )
                        
                        # Distribuir al organizador
                        organizer_prize = self.current_prize * Decimal(organizer_percentage)

                        print("PREMIOS DEL ORGANIZADOR",organizer_prize)
                        print("ORGANIZADOR ",self.organizer)
                        
                        
                        self.organizer.credit_balance += organizer_prize
                        print("BALANCE", self.organizer.credit_balance)
                        self.organizer.save()

                        print("DEPURANDO JUGADOR SALDO ACTUAL 2",winner.credit_balance)
                        
                        Transaction.objects.create(
                            user=self.organizer,
                            amount=organizer_prize,
                            transaction_type='ADMIN_ADD',
                            description=f"Premio por juego {self.name} (parte organizador)",
                            related_game=self
                        )
                        
                        # Distribuir al admin
                        admin = User.objects.filter(is_admin=True).first()
                        if admin:
                            admin_prize = self.current_prize * Decimal(admin_percentage)
                            admin.credit_balance += admin_prize
                            admin.save()
                            
                            Transaction.objects.create(
                                user=admin,
                                amount=admin_prize,
                                transaction_type='ADMIN_ADD',
                                description=f"Premio por juego {self.name} (parte administrador)",
                                related_game=self
                            ) # 2. Acreditar al organizador el valor total de cartones vendidos
                            if total_cards_value > 0:
                                self.organizer.credit_balance += total_cards_value
                                print(total_cards_value)
                                self.organizer.save()
                                
                                Transaction.objects.create(
                                    user=self.organizer,
                                    amount=total_cards_value,
                                    transaction_type='CARDS_REVENUE',
                                    description=f"Ingresos por cartones vendidos en {self.name}",
                                    related_game=self
                                )

                        print("DEPURANDO JUGADOR SALDO ACTUAL 3",winner.credit_balance)
                        
                        return True
                except Exception as e:
                    # Manejar errores en la transacción
                    logger.error(f"Error al distribuir premio: {str(e)}")
                    return False
            return True
        return False
    
    def end_game_manual(self, winner):
        if not self.is_finished:
            self.is_finished = True
            self.winner = winner
            self.current_prize = self.calculate_current_prize()
            
            try:
                with transaction.atomic():
                    # Bloquear registros primero
                    winner = User.objects.select_for_update().get(pk=winner.pk)
                    organizer = User.objects.select_for_update().get(pk=self.organizer.pk)
                    admin = User.objects.filter(is_admin=True).select_for_update().first()
                    
                    # Calcular todos los premios ANTES de modificar balances
                    percentage_settings = PercentageSettings.objects.first()
                    if not percentage_settings or self.current_prize <= 0:
                        return False
                    
                    total_cards_value = self.max_cards_sold * self.card_price
                    
                    # Calcular montos
                    amounts = {
                        'player': self.current_prize * Decimal(percentage_settings.player_percentage / 100),
                        'organizer': self.current_prize * Decimal(percentage_settings.organizer_percentage / 100),
                        'admin': self.current_prize * Decimal(percentage_settings.admin_percentage / 100),
                        'cards': total_cards_value
                    }
                    
                    # Preparar actualizaciones
                    updates = {}
                    
                    # Siempre dar premio al jugador
                    updates[winner] = amounts['player']
                    
                    # Verificar si es organizador
                    if winner == organizer:
                        updates[winner] += amounts['organizer'] + amounts['cards']
                    else:
                        updates[organizer] = amounts['organizer'] + amounts['cards']
                        updates[winner] = amounts['player']
                    
                    # Verificar si es admin (y no es el organizador)
                    if admin and admin != winner and admin != organizer:
                        updates[admin] = amounts['admin']
                    
                    # Aplicar TODAS las actualizaciones en una sola operación por usuario
                    for user, amount in updates.items():
                        user.credit_balance += amount
                        user.save()
                    
                    # Guardar estado del juego
                    self.save()
                    
                    # Registrar transacciones
                    Transaction.objects.create(
                        user=winner,
                        amount=amounts['player'],
                        transaction_type='PRIZE',
                        description=f"Premio por ganar {self.name}",
                        related_game=self
                    )
                    
                    if winner == organizer:
                        Transaction.objects.create(
                            user=winner,
                            amount=amounts['organizer'],
                            transaction_type='ORGANIZER_PRIZE',
                            description=f"Parte organizador de {self.name}",
                            related_game=self
                        )
                        Transaction.objects.create(
                            user=winner,
                            amount=amounts['cards'],
                            transaction_type='CARDS_REVENUE',
                            description=f"Ingresos por cartones en {self.name}",
                            related_game=self
                        )
                    else:
                        Transaction.objects.create(
                            user=organizer,
                            amount=amounts['organizer'],
                            transaction_type='ORGANIZER_PRIZE',
                            description=f"Parte organizador de {self.name}",
                            related_game=self
                        )
                        Transaction.objects.create(
                            user=organizer,
                            amount=amounts['cards'],
                            transaction_type='CARDS_REVENUE',
                            description=f"Ingresos por cartones en {self.name}",
                            related_game=self
                        )
                    
                    if admin and admin != winner and admin != organizer:
                        Transaction.objects.create(
                            user=admin,
                            amount=amounts['admin'],
                            transaction_type='ADMIN_PRIZE',
                            description=f"Parte admin de {self.name}",
                            related_game=self
                        )
                    
                    # Notificación
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f"user_{winner.id}",
                        {
                            'type': 'win_notification',
                            'message': f"¡Ganaste {self.current_prize} créditos en {self.name}",
                            'details': {
                                'player_prize': float(amounts['player']),
                                'organizer_prize': float(amounts['organizer']) if winner == organizer else 0,
                                'admin_prize': float(amounts['admin']) if admin and admin != winner and admin != organizer else 0,
                                'cards_revenue': float(amounts['cards']) if winner == organizer else 0
                            }
                        }
                    )
                    
                    return True
                    
            except Exception as e:
                logger.error(f"Error en end_game: {str(e)}", exc_info=True)
                return False
        return False

    def start_auto_calling(self):
        if self.is_started and not self.is_finished and not self.is_auto_calling:
            self.is_auto_calling = True
            self.save()
            return True
        return False

    def stop_auto_calling(self):
        if self.is_auto_calling:
            self.is_auto_calling = False
            self.save()
            return True
        return False
    
    # Modificar calculate_current_prize para usar initial_cards_sold si el juego ha empezado
    def calculate_current_prize(self):
        """Calcula el premio actual usando el máximo histórico de cartones"""
        total_prize = self.base_prize
        
        # Usamos max_cards_sold que siempre tiene el valor más alto
        print("self.max_cards_sold",self.max_cards_sold)
        if self.progressive_prizes:
            for prize in sorted(self.progressive_prizes, key=lambda x: x['target']):
                if self.max_cards_sold >= prize['target']:
                    total_prize += Decimal(str(prize['prize']))
        
        return total_prize
    
    def check_progressive_prize(self):
        """Verifica premios progresivos usando el máximo histórico"""
        old_prize = self.current_prize
        self.current_prize = self.calculate_current_prize()
        self.save()
        
        if self.progressive_prizes:
            next_target = None
            for prize in sorted(self.progressive_prizes, key=lambda x: x['target']):
                if self.max_cards_sold < prize['target']:
                    next_target = prize['target']
                    break
            
            self.next_prize_target = next_target
            self.save()

        prize_increase = self.current_prize - old_prize
        
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'game_{self.id}',
            {
                'type': 'prize_updated',
                'new_prize': float(self.current_prize),
                'increase_amount': float(prize_increase) if prize_increase > 0 else 0,
                'total_cards': self.max_cards_sold,  # Mostramos el máximo
                'next_target': self.next_prize_target
            }
        )
        
        return prize_increase
            

    
    def save(self, *args, **kwargs):
        """Sobrescribe save para actualizar automáticamente el current_prize y max_cards_sold"""
        # Actualizar max_cards_sold si es necesario
        if self.total_cards_sold > self.max_cards_sold:
            self.max_cards_sold = self.total_cards_sold
        
        # Asegurar que current_prize nunca sea menor que base_prize
        self.current_prize = self.calculate_current_prize()
        if self.current_prize < self.base_prize:
            self.current_prize = self.base_prize
        
        super().save(*args, **kwargs)

class Player(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    cards = models.JSONField(default=list)
    is_winner = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {self.game.name}"

    def generate_card(self):
        card = []
        for _ in range(5):
            row = sorted(random.sample(range(1, 91), 5))
            card.append(row)
        return card

    def check_bingo(self):
        called_numbers_set = set(self.game.called_numbers)
        
        for card in self.cards:
            # Función auxiliar para verificar si un número está marcado (considera 0 como comodín)
            def is_marked(num):
                return num == 0 or num in called_numbers_set
            
            if self.game.winning_pattern == 'HORIZONTAL':
                for row in card:
                    if all(is_marked(num) for num in row):
                        return True
            elif self.game.winning_pattern == 'VERTICAL':
                for col in range(5):
                    if all(is_marked(row[col]) for row in card):
                        return True
            elif self.game.winning_pattern == 'DIAGONAL':
                if all(is_marked(card[i][i]) for i in range(5)) or \
                all(is_marked(card[i][4-i]) for i in range(5)):
                    return True
            elif self.game.winning_pattern == 'FULL':
                if all(is_marked(num) for row in card for num in row):
                    return True
            elif self.game.winning_pattern == 'CORNERS':
                corners = [card[0][0], card[0][4], card[4][0], card[4][4]]
                if all(is_marked(corner) for corner in corners):
                    return True
            elif self.game.winning_pattern == 'CUSTOM' and self.game.custom_pattern:
                # Verificar patrón personalizado
                pattern = self.game.custom_pattern
                for i in range(5):
                    for j in range(5):
                        if pattern[i][j] == 1 and not is_marked(card[i][j]):
                            break
                    else:
                        continue
                    break
                else:
                    return True
        return False

    async def acheck_bingo(self):
        return await sync_to_async(self.check_bingo)()

class ChatMessage(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}: {self.message[:20]}..."

class Transaction(models.Model):
    TRANSACTION_TYPES = [
        ('PURCHASE', 'Compra de cartones'),
        ('ADMIN_ADD', 'Recarga administrativa'),
        ('PRIZE', 'Premio de juego'),
        ('OTHER', 'Otra transacción')
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, default='PURCHASE')
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    related_game = models.ForeignKey(Game, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_transaction_type_display()} - ${self.amount}"

class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-timestamp']
        
    def __str__(self):
        return f"De {self.sender.username} a {self.recipient.username}"

class Raffle(models.Model):
    STATUS_CHOICES = [
        ('WAITING', 'Esperando jugadores'),
        ('IN_PROGRESS', 'En progreso'),
        ('FINISHED', 'Terminada'),
    ]
    
    organizer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='organized_raffles')
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    ticket_price = models.DecimalField(max_digits=10, decimal_places=2)
    prize = models.DecimalField(max_digits=10, decimal_places=2,editable=True,verbose_name="Premio base a distribuir")
    final_prize = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Premio final entregado"
    )
    tickets_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Total recaudado por tickets"
    )
    start_number = models.PositiveIntegerField(default=1)
    end_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    draw_date = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='WAITING')
    winner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    winning_number = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.title
    
    @property
    def total_tickets(self):
        return self.end_number - self.start_number + 1
    
    @property
    def available_tickets(self):
        return self.total_tickets - self.tickets.count()
    
    @property
    def progress_percentage(self):
        return (self.tickets.count() / self.total_tickets) * 100
    
    @property
    def total_tickets(self):
        return self.end_number - self.start_number + 1
    
    @property
    def available_tickets(self):
        return self.total_tickets - self.tickets.count()
    
    @property
    def progress_percentage(self):
        return (self.tickets.count() / self.total_tickets) * 100
    
    def can_be_drawn(self):
        """Determina si la rifa puede ser sorteada"""
        return self.status in ['WAITING', 'IN_PROGRESS'] and self.tickets.exists()
    
    def draw_winner(self):
        """Realiza el sorteo y devuelve el ganador"""
        if not self.can_be_drawn():
            return None
        
        winning_ticket = random.choice(self.tickets.all())
        self.winning_number = winning_ticket.number
        self.winner = winning_ticket.owner
        self.status = 'FINISHED'
        self.save()
        
        return winning_ticket

class Ticket(models.Model):
    raffle = models.ForeignKey(Raffle, on_delete=models.CASCADE, related_name='tickets')
    number = models.PositiveIntegerField()
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tickets')
    purchased_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('raffle', 'number')
    
    def __str__(self):
        return f"Ticket #{self.number} - {self.raffle.title}"

class PercentageSettings(models.Model):
    admin_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10.00, validators=[MinValueValidator(0), MaxValueValidator(100)])
    organizer_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20.00, validators=[MinValueValidator(0), MaxValueValidator(100)])
    player_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=70.00, validators=[MinValueValidator(0), MaxValueValidator(100)])
    last_updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        verbose_name_plural = "Percentage Settings"

    def __str__(self):
        return f"Admin: {self.admin_percentage}%, Organizer: {self.organizer_percentage}%, Player: {self.player_percentage}%"
    

# models.py (opcional, solo si quieres guardar historial)
class FlashMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.user.username}: {self.message[:50]}..."
    
class WithdrawalRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendiente'),
        ('APPROVED', 'Aprobado'),
        ('REJECTED', 'Rechazado'),
        ('COMPLETED', 'Completado'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='withdrawal_requests')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=50)
    account_holder_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)
    transaction_reference = models.CharField(max_length=100, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Solicitud de Retiro'
        verbose_name_plural = 'Solicitudes de Retiro'
    
    def __str__(self):
        return f"Retiro de {self.user.username} - ${self.amount} - {self.get_status_display()}"
    
    def save(self, *args, **kwargs):
        # Si el estado cambia, actualizar la fecha de procesamiento
        super().save(*args, **kwargs)


class BankAccount(models.Model):
    """
    Modelo completamente personalizable para cuentas bancarias/métodos de pago
    """
    title = models.CharField(
        max_length=100, 
        verbose_name="Título/Nombre",
        help_text="Ej: Banco de Venezuela, Zelle, PayPal, Binance, etc."
    )
    details = models.TextField(
        verbose_name="Detalles completos",
        help_text="Información completa que verán los usuarios. Ej: Número de cuenta, titular, cédula, teléfono, email, etc."
    )
    instructions = models.TextField(
        blank=True,
        verbose_name="Instrucciones especiales",
        help_text="Instrucciones específicas para este método (opcional)"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activo",
        help_text="Mostrar este método a los usuarios"
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Orden",
        help_text="Orden de visualización (mayor número = más arriba)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Método de Pago'
        verbose_name_plural = 'Métodos de Pago'
        ordering = ['-order', 'title']

    def __str__(self):
        return f"{self.title} ({'Activo' if self.is_active else 'Inactivo'})"