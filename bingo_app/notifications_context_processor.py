def notifications(request):
    notification = None

    print("PRUEBAS DE NOTIFICACIONES ")
    if 'show_win_notification' in request.session:
        notification = request.session.pop('show_win_notification')
        print("NOTIFICACIONES 2",notification)
    return {'win_notification': notification}