# utils/flash_messages.py (crea este archivo)
def add_flash_message(request, message):
    if not hasattr(request, 'flash_messages'):
        request.flash_messages = []
    request.flash_messages.append(message)
