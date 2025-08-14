from django.contrib import admin

# Register your models here.
from .models import User, Game, Player, ChatMessage

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Game,Message, Player, ChatMessage, PercentageSettings, Raffle, FlashMessage, CreditRequest

# Personalización del UserAdmin
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_organizer', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Datos adicionales', {'fields': ('is_organizer',)}),
    )

from .models import Transaction

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'transaction_type', 'amount', 'created_at')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('user__username', 'description')
    readonly_fields = ('created_at',)

# Registra todos los modelos
admin.site.register(User)
admin.site.register(Game)
admin.site.register(Player)
admin.site.register(ChatMessage)
admin.site.register(PercentageSettings)
admin.site.register(Raffle)
admin.site.register(FlashMessage)
admin.site.register(Message)
admin.site.register(CreditRequest)