# middleware.py (crea este archivo nuevo)
from django.utils.deprecation import MiddlewareMixin

class FlashMessageMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.user.is_authenticated:
            # Mensajes flash almacenados en sesión (sin DB)
            request.flash_messages = request.session.pop('flash_messages', [])
        else:
            request.flash_messages = []

    def process_response(self, request, response):
        if hasattr(request, 'flash_messages') and request.flash_messages:
            request.session['flash_messages'] = request.flash_messages
        return response