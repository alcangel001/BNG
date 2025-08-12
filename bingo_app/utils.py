import random
 

# def generate_bingo_card():
#     """Genera un cartón de Bingo 5x5 con números únicos"""
#     card = []
#     for _ in range(5):
#         row = sorted(random.sample(range(1, 75), 5))
#         card.append(row)
#     return card

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

def get_pattern_description(pattern):
    descriptions = {
        'HORIZONTAL': 'Gana completando una línea horizontal',
        'VERTICAL': 'Gana completando una línea vertical',
        'DIAGONAL': 'Gana completando las dos diagonales (X)',
        'FULL': 'Gana completando todo el cartón',
        'CORNERS': 'Gana marcando las cuatro esquinas'
    }
    return descriptions.get(pattern, '')

    