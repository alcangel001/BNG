from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import MinValueValidator
import json
from .models import User, Game, CreditRequest, Raffle, PercentageSettings

class RegistrationForm(UserCreationForm):
    is_organizer = forms.BooleanField(
        required=False,
        label='¿Eres un organizador? (Puedes crear juegos)'
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'is_organizer')

class GameForm(forms.ModelForm):
    auto_call_interval = forms.IntegerField(
        initial=5,
        min_value=1,
        max_value=60,
        help_text="Segundos entre llamadas automáticas"
    )
    
    class Meta:
        model = Game
        fields = ['name', 'password', 'entry_price', 'card_price', 
                 'max_cards_per_player', 'winning_pattern',
                'base_prize', 'auto_call_interval', 'progressive_prizes','custom_pattern']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['progressive_prizes'].required = False
        self.fields['progressive_prizes'].widget = forms.HiddenInput()  # Ocultar campo raw

        self.fields['custom_pattern'].required = False
        
        # Añadir campo para subir un archivo JSON con el patrón
        self.fields['pattern_file'] = forms.FileField(
            required=False,
            help_text="Sube un archivo JSON con el patrón personalizado"
        )
        
        # Campo para la interfaz de usuario (si usas JavaScript para manejar los premios)
        self.fields['progressive_prizes_json'] = forms.CharField(
            required=False,
            widget=forms.HiddenInput()
        )
    
    def clean(self):
        cleaned_data = super().clean()
        winning_pattern = cleaned_data.get('winning_pattern')
        custom_pattern = cleaned_data.get('custom_pattern')
        pattern_file = cleaned_data.get('pattern_file')

        if winning_pattern == 'CUSTOM':
            if not custom_pattern and not pattern_file:
                raise forms.ValidationError("Debes proporcionar un patrón personalizado o subir un archivo")
            
            if pattern_file:
                try:
                    pattern_data = json.load(pattern_file)
                    cleaned_data['custom_pattern'] = pattern_data
                except json.JSONDecodeError:
                    raise forms.ValidationError("El archivo debe ser un JSON válido")
        
        
        
        # Procesar premios progresivos
        progressive_prizes_json = self.data.get('progressive_prizes_json')
        if progressive_prizes_json:
            try:
                prizes = json.loads(progressive_prizes_json)
                # Validar estructura
                for prize in prizes:
                    if not all(key in prize for key in ['target', 'prize']):
                        raise forms.ValidationError("Formato de premios progresivos inválido")
                cleaned_data['progressive_prizes'] = prizes
            except (json.JSONDecodeError, TypeError):
                raise forms.ValidationError("Formato de premios progresivos inválido")
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Establecer valores por defecto
        if not instance.current_prize:
            instance.current_prize = instance.base_prize
        
        if commit:
            instance.save()
            self.save_m2m()  # Importante para relaciones ManyToMany si las hay
        
        return instance
        
class CreditRequestForm(forms.ModelForm):
    class Meta:
        model = CreditRequest
        fields = ['amount', 'proof']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '1',
                'step': '0.01'
            }),
            'proof': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*,.pdf'
            })
        }

class RaffleForm(forms.ModelForm):
    class Meta:
        model = Raffle
        fields = ['title', 'description', 'ticket_price', 'prize', 
                 'start_number', 'end_number', 'draw_date']
        widgets = {
            'draw_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'ticket_price': forms.NumberInput(attrs={
                'min': '0.50',
                'step': '0.50'
            }),
            'prize': forms.NumberInput(attrs={
                'min': '1',
                'step': '1'
            }),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('start_number')
        end = cleaned_data.get('end_number')
        
        if start and end and start >= end:
            raise forms.ValidationError("El número final debe ser mayor que el inicial")
        
        return cleaned_data

class BuyTicketForm(forms.Form):
    number = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Número de ticket'
        })
    )

class PercentageSettingsForm(forms.ModelForm):
    class Meta:
        model = PercentageSettings
        fields = ['admin_percentage', 'organizer_percentage', 'player_percentage']
        widgets = {
            'admin_percentage': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '100'
            }),
            'organizer_percentage': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '100'
            }),
            'player_percentage': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '100'
            }),
        }
        labels = {
            'admin_percentage': 'Porcentaje administrador (%)',
            'organizer_percentage': 'Porcentaje organizador (%)',
            'player_percentage': 'Porcentaje jugador (%)'
        }

    def clean(self):
        cleaned_data = super().clean()
        admin = cleaned_data.get('admin_percentage')
        organizer = cleaned_data.get('organizer_percentage')
        player = cleaned_data.get('player_percentage')
        
        if admin and organizer and player:
            total = admin + organizer + player
            if abs(total - 100) > 0.01:  # Permitir pequeñas diferencias por redondeo
                raise forms.ValidationError(
                    f"La suma de porcentajes debe ser 100%. Actual: {total:.2f}%"
                )
        
        return cleaned_data