# bingo_app/context_processors.py
from .models import CreditRequestNotification

def notifications_global(request):
    if request.user.is_authenticated:
        return {
            'global_unread_count': request.user.credit_notifications.filter(is_read=False).count(),
            'global_unread_notifications': request.user.credit_notifications.filter(is_read=False)[:5]
        }
    return {}