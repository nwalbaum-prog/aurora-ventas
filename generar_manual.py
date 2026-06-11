"""
Genera el Manual de Usuario completo de Aurora Bakers en PDF.
Uso: python generar_manual.py
Salida: manual_aurora_bakers.pdf
"""
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from datetime import date
import os

FONT_DIR = r'C:\Windows\Fonts'
ARIAL    = os.path.join(FONT_DIR, 'arial.ttf')
ARIALB   = os.path.join(FONT_DIR, 'arialbd.ttf')
ARIALI   = os.path.join(FONT_DIR, 'ariali.ttf')

# ── Paleta Aurora ──────────────────────────────────────────────────────────────
C_AURORA   = (200, 145,  74)   # naranja Aurora
C_DARK     = ( 15,  15,  15)   # fondo oscuro
C_TEXT     = ( 30,  30,  30)   # texto principal
C_MUTED    = (100, 100, 100)   # texto secundario
C_SURFACE  = (248, 248, 248)   # fondo filas alternas
C_GREEN    = ( 22, 101,  52)   # success
C_YELLOW   = (146,  64,  14)   # warning
C_RED      = (153,  27,  27)   # danger
C_BLUE     = ( 29,  78, 136)   # info


class Manual(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'A4')
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)
        self._page_label = ''
        self.add_font('Arial',  '',  ARIAL)
        self.add_font('Arial',  'B', ARIALB)
        self.add_font('Arial',  'I', ARIALI)

    # ── Cabecera / Pie ─────────────────────────────────────────────────────────
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('Arial', 'B', 8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 6, 'Manual de Usuario - Aurora Bakers', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*C_AURORA)
        self.set_line_width(0.4)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font('Arial', '', 8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 5, f'Pagina {self.page_no() - 1}', align='C')

    # ── Helpers de estilo ──────────────────────────────────────────────────────
    def titulo_seccion(self, numero, texto, color=C_AURORA):
        self.ln(4)
        self.set_fill_color(*color)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 13)
        self.cell(0, 10, f'  {numero}  {texto}', fill=True,
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)
        self.set_text_color(*C_TEXT)

    def subtitulo(self, texto):
        self.ln(3)
        self.set_font('Arial', 'B', 11)
        self.set_text_color(*C_AURORA)
        self.cell(0, 7, texto, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*C_TEXT)

    def parrafo(self, texto):
        self.set_font('Arial', '', 10)
        self.set_text_color(*C_TEXT)
        self.multi_cell(170, 5.5, texto)
        self.ln(1)

    def bullet(self, texto, nivel=0):
        self.set_font('Arial', '', 10)
        self.set_text_color(*C_TEXT)
        indent = 5 + nivel * 8
        marker = '-' if nivel == 0 else '*'
        self.set_x(20 + indent)
        self.multi_cell(170 - indent, 5.5, f'{marker}  {texto}')

    def nota(self, texto, tipo='info'):
        colores = {
            'info':    (C_BLUE,   (219, 234, 254)),
            'tip':     (C_GREEN,  (220, 252, 231)),
            'warning': (C_YELLOW, (254, 243, 199)),
        }
        border_c, bg_c = colores.get(tipo, colores['info'])
        self.ln(2)
        self.set_fill_color(*bg_c)
        self.set_draw_color(*border_c)
        self.set_line_width(0.3)
        y0 = self.get_y()
        self.rect(20, y0, 170, 1)   # top border accent
        self.set_fill_color(*bg_c)
        self.set_font('Arial', '', 9.5)
        self.set_text_color(*border_c)
        self.multi_cell(170, 5.2, f'   {texto}', fill=True)
        self.ln(2)
        self.set_text_color(*C_TEXT)

    def tabla_simple(self, cabeceras, filas, anchos=None):
        if anchos is None:
            total = 170
            w = total // len(cabeceras)
            anchos = [w] * len(cabeceras)
        # Header
        self.set_fill_color(*C_AURORA)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 9)
        for i, h in enumerate(cabeceras):
            self.cell(anchos[i], 7, f'  {h}', border=0, fill=True)
        self.ln()
        # Filas
        self.set_text_color(*C_TEXT)
        self.set_font('Arial', '', 9)
        for idx, fila in enumerate(filas):
            if idx % 2 == 0:
                self.set_fill_color(*C_SURFACE)
            else:
                self.set_fill_color(255, 255, 255)
            for i, celda in enumerate(fila):
                self.cell(anchos[i], 6.5, f'  {celda}', border=0, fill=True)
            self.ln()
        self.ln(2)

    def paso(self, num, texto):
        self.set_font('Arial', 'B', 10)
        self.set_text_color(*C_AURORA)
        x0 = self.get_x()
        self.cell(8, 6, str(num))
        self.set_font('Arial', '', 10)
        self.set_text_color(*C_TEXT)
        self.multi_cell(162, 6, texto)

    def linea_divisoria(self):
        self.ln(3)
        self.set_draw_color(*C_AURORA)
        self.set_line_width(0.2)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(4)


# ══════════════════════════════════════════════════════════════════════════════
def build_pdf():
    pdf = Manual()

    # ── PORTADA ────────────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_fill_color(*C_DARK)
    pdf.rect(0, 0, 210, 297, 'F')

    pdf.set_y(70)
    pdf.set_font('Arial', 'B', 36)
    pdf.set_text_color(*C_AURORA)
    pdf.cell(0, 14, 'AURORA BAKERS', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font('Arial', '', 16)
    pdf.set_text_color(220, 220, 220)
    pdf.cell(0, 10, 'Manual Completo del Sistema', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(8)
    pdf.set_draw_color(*C_AURORA)
    pdf.set_line_width(1)
    pdf.line(55, pdf.get_y(), 155, pdf.get_y())

    pdf.ln(10)
    pdf.set_font('Arial', '', 11)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 7, 'Gestion de Ventas, Produccion, Inventario y Clientes', align='C',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(240)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, f'Fecha: {date.today().strftime("%d de %B de %Y")}', align='C',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, 'Sistema: Flask + SQLite  |  Acceso: http://localhost:5000', align='C')

    # ── INDICE ─────────────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_text_color(*C_TEXT)
    pdf.set_font('Arial', 'B', 16)
    pdf.set_text_color(*C_AURORA)
    pdf.cell(0, 10, 'Contenido', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*C_AURORA)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)

    secciones = [
        ('1', 'Introduccion y Acceso al Sistema'),
        ('2', 'Flujo Completo de un Dia de Trabajo'),
        ('3', 'Configuracion Inicial del Sistema'),
        ('4', 'Modulo POS — Punto de Venta'),
        ('5', 'Modulo Ventas (ERP)'),
        ('6', 'Modulo Clientes'),
        ('7', 'Modulo Productos'),
        ('8', 'Modulo Produccion'),
        ('9', 'Modulo Inventario'),
        ('10', 'Modulo Gastos'),
        ('11', 'Modulo Finanzas'),
        ('12', 'Modulo Despacho'),
        ('13', 'Modulo Suscripciones'),
        ('14', 'Modulo Reportes y Analisis'),
        ('15', 'Modulo CRM'),
        ('16', 'Dashboard Movil'),
        ('17', 'Agenda'),
        ('18', 'Gestion de Usuarios'),
        ('19', 'Flujos Completos con Ejemplos'),
        ('20', 'Preguntas Frecuentes'),
    ]
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(*C_TEXT)
    for num, titulo in secciones:
        pdf.cell(12, 7, num)
        pdf.cell(0, 7, titulo, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ══════════════════════════════════════════════════════════════════════════
    # 1. INTRODUCCION
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('1', 'Introduccion y Acceso al Sistema')

    pdf.parrafo(
        'Aurora Bakers es un sistema de gestion integral para panaderias y pastelerias. '
        'Permite administrar ventas, produccion, inventario, clientes, despachos, finanzas '
        'y mucho mas desde un navegador web, tanto desde computador como desde celular.'
    )

    pdf.subtitulo('Como ingresar al sistema')
    pdf.parrafo('Abre un navegador y navega a la direccion del sistema:')
    pdf.nota('Local: http://localhost:5000    |    En internet: URL entregada por el administrador', 'info')

    pdf.parrafo('Ingresa con tu usuario y contrasena. Al cerrar el navegador la sesion se cierra automaticamente por seguridad.')

    pdf.subtitulo('Modulos disponibles')
    pdf.tabla_simple(
        ['Modulo', 'Para que sirve', 'Quien lo usa'],
        [
            ['POS / Caja', 'Venta rapida en mostrador', 'Cajero'],
            ['Ventas', 'Pedidos completos con detalle', 'Vendedor / Admin'],
            ['Clientes', 'Base de datos de clientes', 'Vendedor / Admin'],
            ['Productos', 'Catalogo, precios, fichas', 'Admin'],
            ['Produccion', 'Plan diario de hornado', 'Maestro panadero'],
            ['Inventario', 'Insumos y bodega', 'Admin / Produccion'],
            ['Gastos', 'Egresos y costos fijos', 'Admin / Contador'],
            ['Finanzas', 'P&L y cobros pendientes', 'Dueno / Admin'],
            ['Despacho', 'Seguimiento de entregas', 'Repartidor / Admin'],
            ['Suscripciones', 'Planes de entrega recurrente', 'Vendedor'],
            ['Reportes', 'Analisis de ventas', 'Dueno / Admin'],
            ['CRM', 'Captacion de nuevos clientes', 'Vendedor'],
            ['Agenda', 'Tareas y recordatorios', 'Todo el equipo'],
            ['Dashboard', 'KPIs en tiempo real (celular)', 'Dueno'],
        ],
        [45, 80, 45]
    )

    pdf.subtitulo('Navegacion')
    pdf.parrafo('La barra lateral izquierda muestra solo los modulos habilitados para tu usuario. '
                'Haz clic en cualquier modulo para acceder. Los administradores ven todos los modulos.')

    # ══════════════════════════════════════════════════════════════════════════
    # 2. FLUJO COMPLETO DE UN DIA
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('2', 'Flujo Completo de un Dia de Trabajo')

    pdf.parrafo(
        'A continuacion se describe el flujo tipico de un dia en Aurora Bakers, '
        'mostrando como se conectan todos los modulos del sistema.'
    )

    pasos_dia = [
        ('MANANA — Preparacion', C_BLUE, [
            ('7:00 am', 'Ingresar a Produccion', 'Revisar el plan del dia. Los pedidos pendientes de despacho aparecen automaticamente. Agregar productos adicionales si es necesario.'),
            ('7:15 am', 'Revisar Inventario', 'Verificar que hay stock suficiente de ingredientes. El sistema alerta en rojo los insumos bajo el minimo.'),
            ('8:00 am', 'Confirmar Hornada', 'Una vez que el pan sale del horno, presionar "Confirmar Produccion". Esto suma stock a los productos y descuenta ingredientes del inventario.'),
        ]),
        ('DURANTE EL DIA — Ventas', C_GREEN, [
            ('9:00 am', 'Abrir Caja POS', 'Ir a POS / Caja, ingresar el monto inicial de efectivo y abrir el turno.'),
            ('9:01+',   'Ventas en mostrador', 'Usar el modulo POS para registrar cada venta. Buscar producto, agregar al carrito, cobrar y emitir boleta.'),
            ('Continuo', 'Pedidos online o por encargo', 'Registrar en Ventas (ERP) los pedidos que requieren despacho o factura con mas detalle.'),
            ('Continuo', 'Gestionar Despachos', 'En Despacho marcar los pedidos como "En preparacion" y luego "Despachado" al salir.'),
        ]),
        ('TARDE — Cierre y Analisis', C_AURORA, [
            ('6:00 pm', 'Cerrar Caja', 'En POS / Caja contar el efectivo real y cerrar el turno. El sistema calcula diferencias.'),
            ('6:15 pm', 'Registrar Gastos', 'Ingresar cualquier compra o gasto del dia en el modulo Gastos.'),
            ('6:30 pm', 'Revisar Finanzas', 'Verificar cobros pendientes y marcar pagos recibidos.'),
            ('6:45 pm', 'Dashboard Movil', 'Desde el celular revisar el resumen del dia: ventas, margen, top productos.'),
        ]),
    ]

    for bloque, color, items in pasos_dia:
        pdf.set_fill_color(*color)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, f'  {bloque}', fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*C_TEXT)
        pdf.ln(1)
        for hora, modulo, desc in items:
            pdf.set_font('Arial', 'B', 9)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(22, 6, f'  {hora}', fill=True)
            pdf.set_fill_color(*C_SURFACE)
            pdf.cell(35, 6, f'  {modulo}', fill=True)
            pdf.set_font('Arial', '', 9)
            pdf.cell(0, 6, f'  {desc}', fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    pdf.nota('El stock de productos sube cuando confirmas produccion y baja cuando registras una venta. '
             'Asi siempre tienes un inventario actualizado en tiempo real.', 'tip')

    # ══════════════════════════════════════════════════════════════════════════
    # 3. CONFIGURACION INICIAL
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('3', 'Configuracion Inicial del Sistema')

    pdf.parrafo('Antes de comenzar a operar, el administrador debe configurar los datos basicos del negocio.')

    pdf.subtitulo('Datos del Negocio  (Menu > Configuracion)')
    pdf.tabla_simple(
        ['Campo', 'Ejemplo', 'Para que se usa'],
        [
            ['Nombre del negocio', 'Aurora Bakers SpA', 'Aparece en boletas y reportes'],
            ['RUT', '76.123.456-7', 'Boletas electronicas y documentos'],
            ['Giro', 'Panaderia y pasteleria', 'Informacion legal'],
            ['Direccion', 'Av. Las Flores 1234, Santiago', 'Datos de contacto'],
            ['Telefono', '+56 9 8765 4321', 'Datos de contacto'],
            ['Email', 'hola@aurorabakers.cl', 'Comunicaciones'],
            ['Dias de despacho', 'Lunes, Miercoles, Viernes', 'Controla fechas disponibles'],
        ],
        [45, 55, 70]
    )

    pdf.subtitulo('Crear Usuarios  (Menu > Usuarios)')
    pdf.parrafo('Crea un usuario para cada miembro del equipo. Asigna el rol y los modulos a los que puede acceder.')
    pdf.tabla_simple(
        ['Rol', 'Acceso', 'Ejemplo de uso'],
        [
            ['Administrador', 'Todos los modulos', 'Dueno del negocio'],
            ['Usuario', 'Solo los modulos asignados', 'Cajero, repartidor, panadero'],
        ],
        [35, 60, 75]
    )
    pdf.nota('Los usuarios solo ven en el menu los modulos que tienen habilitados. El administrador asigna permisos desde Usuarios.', 'tip')

    pdf.subtitulo('Cargar Productos  (Menu > Productos)')
    pdf.parrafo('Agrega todos los productos que vendes con nombre, precio de venta, costo y stock inicial.')

    pdf.subtitulo('Cargar Inventario  (Menu > Inventario)')
    pdf.parrafo('Carga los ingredientes y insumos con stock actual y nivel de alerta. Usa las bodegas correctas:')
    pdf.bullet('Ingredientes: harina, levadura, sal, mantequilla, azucar, etc.')
    pdf.bullet('Productos Terminados: panes listos para venta directa')
    pdf.bullet('Limpieza: detergente, escobillas, guantes, etc.')
    pdf.bullet('Insumos de Produccion: moldes, papel mantequilla, bolsas, etc.')

    # ══════════════════════════════════════════════════════════════════════════
    # 4. POS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('4', 'Modulo POS — Punto de Venta')

    pdf.parrafo(
        'El POS es la interfaz de caja rapida. Permite registrar ventas en mostrador en segundos '
        'sin necesidad de llenar un formulario completo.'
    )

    pdf.subtitulo('Abrir el turno de caja')
    pdf.paso(1, 'Ir a POS / Caja en el menu lateral.')
    pdf.paso(2, 'Ingresar el monto inicial de efectivo en caja (fondo de cambio).')
    pdf.paso(3, 'Hacer clic en "Abrir Turno". El sistema registra la hora de apertura.')
    pdf.ln(3)
    pdf.nota('Ejemplo: Abres con $10.000 en efectivo como fondo de cambio.', 'info')

    pdf.subtitulo('Realizar una venta')
    pdf.paso(1, 'En la pantalla del cajero, busca el producto por nombre o haz clic en un producto frecuente.')
    pdf.paso(2, 'El producto se agrega al carrito derecho con cantidad 1. Puedes cambiar la cantidad.')
    pdf.paso(3, 'Si hay promociones activas, el sistema las aplica automaticamente.')
    pdf.paso(4, 'Selecciona el medio de pago: Efectivo o Tarjeta.')
    pdf.paso(5, 'Si es efectivo, ingresa el monto recibido del cliente. El sistema calcula el vuelto.')
    pdf.paso(6, 'Haz clic en "Cobrar". Se registra la venta y se descuenta el stock de los productos.')
    pdf.ln(3)

    pdf.parrafo('Ejemplo completo:')
    pdf.tabla_simple(
        ['Paso', 'Accion', 'Resultado'],
        [
            ['1', 'Cliente pide 2 Hogazas y 1 Pan Molde', ''],
            ['2', 'Clic en "Hogaza Campesina" x2', 'Carrito: $8.400'],
            ['3', 'Clic en "Pan Molde Blanco" x1', 'Carrito: $12.600'],
            ['4', 'Seleccionar "Efectivo"', ''],
            ['5', 'Cliente paga con $15.000', 'Vuelto: $2.400'],
            ['6', 'Clic "Cobrar"', 'Venta registrada, stock descontado'],
        ],
        [12, 80, 78]
    )

    pdf.subtitulo('Cerrar el turno')
    pdf.paso(1, 'Ir a POS / Caja.')
    pdf.paso(2, 'Contar el efectivo fisico en caja.')
    pdf.paso(3, 'Ingresar el monto contado y hacer clic en "Cerrar Turno".')
    pdf.paso(4, 'El sistema muestra el resumen: ventas efectivo, tarjeta, total esperado y diferencia.')
    pdf.ln(3)
    pdf.nota('Si hay diferencia entre lo esperado y lo contado, revisa si falta registrar algun vuelto o venta.', 'warning')

    pdf.subtitulo('Gestionar Promociones')
    pdf.parrafo('Desde POS / Caja puedes crear y administrar promociones:')
    pdf.tabla_simple(
        ['Tipo', 'Ejemplo'],
        [
            ['Descuento porcentual', '10% off en todas las ventas los lunes'],
            ['Monto fijo', '$500 de descuento en compras sobre $5.000'],
        ],
        [60, 110]
    )
    pdf.parrafo('Las promociones se aplican automaticamente en el POS dentro del rango de fechas configurado.')

    # ══════════════════════════════════════════════════════════════════════════
    # 5. VENTAS ERP
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('5', 'Modulo Ventas (ERP)')

    pdf.parrafo(
        'El modulo Ventas es el registro completo de pedidos. A diferencia del POS, permite '
        'agregar mas detalles: cliente, canal de venta, fecha de despacho, notas y estado de pago.'
    )

    pdf.subtitulo('Crear una venta')
    pdf.tabla_simple(
        ['Campo', 'Descripcion', 'Ejemplo'],
        [
            ['Fecha', 'Fecha del pedido', '2026-05-02'],
            ['Cliente', 'Seleccionar o dejar en blanco', 'Cafe El Bosque'],
            ['Canal', 'Como llego el pedido', 'Local / Online / Suscripcion'],
            ['Tipo cliente', 'Precio a aplicar', 'Cliente / Mayorista'],
            ['Productos', 'Que se vende y cuanto', '5 Hogazas + 10 Pan Molde'],
            ['Despacho', 'Con despacho o retiro', 'Con despacho — 2026-05-03'],
            ['Estado pago', 'Si ya pago o no', 'Pagado / Pendiente'],
            ['Notas', 'Instrucciones especiales', 'Sin sal en 2 hogazas'],
        ],
        [35, 75, 60]
    )

    pdf.subtitulo('Panel de KPIs')
    pdf.parrafo('Al abrir Ventas se muestra un resumen del dia:')
    pdf.bullet('Ventas de hoy, esta semana y este mes')
    pdf.bullet('Cobros pendientes (ventas no pagadas)')
    pdf.bullet('Despachos pendientes para hoy')
    pdf.bullet('Stock critico: productos con poco stock')

    pdf.subtitulo('Canales de venta')
    pdf.tabla_simple(
        ['Canal', 'Cuando usarlo'],
        [
            ['Local', 'Venta presencial o retiro en tienda'],
            ['Online', 'Pedido por WhatsApp, Instagram, web'],
            ['Suscripcion', 'Entrega recurrente programada'],
            ['Delivery', 'Despacho a domicilio por plataforma'],
        ],
        [40, 130]
    )

    pdf.nota('Al crear una venta con canal "Suscripcion" y cliente seleccionado, el sistema crea automaticamente una suscripcion para ese cliente.', 'tip')

    # ══════════════════════════════════════════════════════════════════════════
    # 6. CLIENTES
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('6', 'Modulo Clientes')

    pdf.parrafo('Gestiona tu base de datos de clientes: minoristas, mayoristas y suscriptores.')

    pdf.subtitulo('Datos del cliente')
    pdf.tabla_simple(
        ['Campo', 'Descripcion'],
        [
            ['Nombre', 'Nombre completo o razon social'],
            ['Tipo', 'Minorista (precio normal) o Mayorista (precio mayorista)'],
            ['RUT', 'Para boletas y facturas'],
            ['Telefono / Email', 'Contacto directo'],
            ['Direccion', 'Para despachos a domicilio'],
            ['Notas', 'Preferencias, instrucciones especiales'],
        ],
        [35, 135]
    )

    pdf.subtitulo('Historial de compras')
    pdf.parrafo('Al hacer clic en cualquier cliente puedes ver su historial completo: cada venta, monto y fecha. '
                'El sistema muestra total de compras y cuando fue la ultima.')

    pdf.subtitulo('Suscriptores')
    pdf.parrafo('Los clientes con suscripcion activa aparecen con un badge especial. '
                'Desde el historial puedes ver su plan y frecuencia de entregas.')

    # ══════════════════════════════════════════════════════════════════════════
    # 7. PRODUCTOS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('7', 'Modulo Productos')

    pdf.parrafo('Administra el catalogo de productos: precios, costos, stock y fichas tecnicas.')

    pdf.subtitulo('Agregar un producto')
    pdf.tabla_simple(
        ['Campo', 'Descripcion', 'Ejemplo'],
        [
            ['Nombre', 'Nombre del producto', 'Hogaza Campesina'],
            ['Categoria', 'Grupo del producto', 'Pan artesanal'],
            ['Precio venta', 'Precio al cliente minorista', '$4.200'],
            ['Precio mayorista', 'Precio a clientes mayoristas', '$3.500'],
            ['Costo', 'Costo de produccion', '$465'],
            ['Stock', 'Unidades disponibles ahora', '10'],
            ['Unidad', 'Como se mide', 'unidad / kg / caja'],
        ],
        [38, 75, 57]
    )

    pdf.parrafo('El margen se calcula automaticamente: Margen = (Precio - Costo) / Precio x 100')
    pdf.parrafo('Los margenes aparecen en colores: verde (bueno), amarillo (ajustado), rojo (negativo).')

    pdf.subtitulo('Ficha Tecnica (Receta con % Panadero)')
    pdf.parrafo(
        'La ficha tecnica permite registrar la receta de cada producto usando el sistema '
        'de porcentaje panadero, donde la harina es siempre el 100% y el resto de ingredientes '
        'se expresan como porcentaje de la harina total.'
    )

    pdf.paso(1, 'Haz clic en el icono de receta (libro) en la fila del producto.')
    pdf.paso(2, 'Ingresa cuantos kg de harina se usan por unidad producida.')
    pdf.paso(3, 'Selecciona el tipo de harina: Blanca, Integral o Mixta (combinacion).')
    pdf.paso(4, 'Agrega los demas ingredientes con su porcentaje respecto a la harina.')
    pdf.paso(5, 'El sistema calcula automaticamente los gramos por unidad.')
    pdf.paso(6, 'Guarda la receta.')
    pdf.ln(3)

    pdf.parrafo('Ejemplo — Hogaza Campesina (produce 1 unidad de 800g):')
    pdf.tabla_simple(
        ['Ingrediente', '% Panadero', 'g / unidad'],
        [
            ['Harina Blanca (base)', '100%', '450g'],
            ['Agua', '72%', '324g'],
            ['Sal', '2%', '9g'],
            ['Levadura natural', '20%', '90g'],
            ['Aceite de oliva', '3%', '14g'],
        ],
        [65, 55, 50]
    )
    pdf.nota('Al confirmar la produccion del dia, el sistema descuenta automaticamente estos ingredientes del inventario.', 'tip')

    # ══════════════════════════════════════════════════════════════════════════
    # 8. PRODUCCION
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('8', 'Modulo Produccion')

    pdf.parrafo(
        'Planifica cuanto producir cada dia en base a los pedidos pendientes y ventas historicas.'
    )

    pdf.subtitulo('Como funciona')
    pdf.bullet('Los pedidos con despacho para hoy aparecen automaticamente en el panel derecho.')
    pdf.bullet('El plan de produccion se puede cargar desde esos pedidos con un clic.')
    pdf.bullet('Puedes agregar productos adicionales manualmente.')
    pdf.bullet('Al terminar de hornear, confirmas la produccion y el sistema actualiza todo.')

    pdf.subtitulo('Confirmar la Produccion')
    pdf.paso(1, 'Selecciona la fecha (por defecto hoy).')
    pdf.paso(2, 'Revisa que el plan este completo con todos los productos y cantidades.')
    pdf.paso(3, 'Haz clic en "Confirmar Produccion".')
    pdf.paso(4, 'El sistema: suma el stock producido a cada producto, descuenta ingredientes del inventario y alerta si algun ingrediente queda bajo el minimo.')
    pdf.ln(3)

    pdf.nota('Si un producto no tiene ficha tecnica configurada, igual se suma al stock al confirmar. El descuento de ingredientes solo ocurre con fichas tecnicas completas.', 'warning')

    pdf.subtitulo('Copiar plan del dia anterior')
    pdf.parrafo('Si produces lo mismo cada dia, usa el boton "Copiar" para traer el plan del dia anterior y solo ajustar cantidades.')

    pdf.subtitulo('Ejemplo de un dia tipico')
    pdf.tabla_simple(
        ['Producto', 'Cantidad a producir', 'Origen'],
        [
            ['Hogaza Campesina', '15 unidades', 'Plan diario fijo'],
            ['Pan Molde Blanco', '20 unidades', 'Pedido Cafe El Bosque'],
            ['Ciabatta', '30 unidades', 'Stock habitual'],
            ['Hogaza Integral', '8 unidades', 'Pedido suscripcion'],
        ],
        [65, 55, 50]
    )

    # ══════════════════════════════════════════════════════════════════════════
    # 9. INVENTARIO
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('9', 'Modulo Inventario')

    pdf.parrafo('Controla el stock de todos los insumos organizados por tipo de bodega.')

    pdf.subtitulo('Bodegas disponibles')
    pdf.tabla_simple(
        ['Bodega', 'Que guardar', 'Unidades tipicas'],
        [
            ['Ingredientes', 'Harina, agua, sal, levadura, mantequilla', 'kg, litros, g'],
            ['Prod. Terminados', 'Panes listos para entregar o vender', 'unidades, cajas'],
            ['Limpieza', 'Detergente, esponjas, guantes, escobillas', 'unidades, bolsas'],
            ['Insumos Produccion', 'Moldes, papel mantequilla, bolsas de empaque', 'rollos, unidades'],
        ],
        [42, 80, 48]
    )

    pdf.subtitulo('Indicadores de stock')
    pdf.bullet('Verde (OK): stock por encima del minimo configurado')
    pdf.bullet('Amarillo (Reponer): stock bajo el minimo pero mayor que cero')
    pdf.bullet('Rojo (Sin stock): stock en cero o negativo')

    pdf.subtitulo('Ajustar stock manualmente')
    pdf.parrafo(
        'Usa el boton "+/−" (Ajustar stock) para corregir el stock sin necesidad de crear una produccion o venta. '
        'Ingresa un numero positivo para sumar o negativo para restar.'
    )
    pdf.nota('Ejemplo: llegaron 25 kg de harina. Abre el ajuste de "Harina Blanca" y escribe 25. El sistema suma al stock actual.', 'info')

    pdf.subtitulo('Alerta de reposicion')
    pdf.parrafo('En la parte superior de cada pestaña, el sistema muestra que items necesitan reposicion urgente '
                '(sin stock) en rojo y cuales estan proximos a agotarse en amarillo.')

    # ══════════════════════════════════════════════════════════════════════════
    # 10. GASTOS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('10', 'Modulo Gastos')

    pdf.parrafo('Registra todos los egresos del negocio: compras de ingredientes, servicios, arriendos, etc.')

    pdf.subtitulo('Registrar un gasto')
    pdf.tabla_simple(
        ['Campo', 'Descripcion', 'Ejemplo'],
        [
            ['Fecha', 'Cuando ocurrio el gasto', '2026-05-02'],
            ['Descripcion', 'Que se compro o pago', 'Compra harina Fortaleza 50kg'],
            ['Categoria', 'Tipo de gasto', 'Ingredientes / Arriendo / Servicios'],
            ['Monto', 'Cuanto se pago', '$45.000'],
            ['Proveedor', 'A quien se le pago', 'Molinera Sur'],
            ['Gasto fijo', 'Si se repite cada mes', 'Si (checkbox)'],
        ],
        [35, 75, 60]
    )

    pdf.subtitulo('Gastos fijos mensuales')
    pdf.parrafo(
        'Marca como "Gasto fijo mensual" los pagos que se repiten todos los meses: arriendo, '
        'internet, servicios basicos, sueldos, etc. Una vez marcados:'
    )
    pdf.bullet('Aparecen en el panel de "Gastos Fijos" con el total mensual estimado.')
    pdf.bullet('Puedes hacer clic en "Aplicar al mes actual" para crearlos con la fecha de hoy con un solo clic.')
    pdf.bullet('Si ya los aplicaste este mes, el sistema no los duplica.')

    pdf.nota('Ejemplo: Arriendo local $400.000/mes, Internet $25.000/mes. Al inicio de cada mes haz clic en "Aplicar al mes actual" y ambos se crean automaticamente.', 'tip')

    # ══════════════════════════════════════════════════════════════════════════
    # 11. FINANZAS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('11', 'Modulo Finanzas')

    pdf.parrafo('Vista ejecutiva del estado financiero del negocio: ingresos, egresos, ganancia y cobros pendientes.')

    pdf.subtitulo('Panel de P&L (Ganancias y Perdidas)')
    pdf.parrafo('El grafico muestra mes a mes:')
    pdf.bullet('Ingresos (ventas totales)')
    pdf.bullet('Egresos (gastos registrados)')
    pdf.bullet('Ganancia neta (Ingresos − Egresos)')

    pdf.parrafo('Puedes filtrar por los ultimos 3, 6 o 12 meses.')

    pdf.subtitulo('Cobros pendientes')
    pdf.parrafo(
        'Lista todas las ventas marcadas como "Pendiente" de pago. '
        'Para cada una puedes hacer clic en "Marcar como pagado" cuando el cliente transfiera o pague.'
    )

    pdf.subtitulo('Margenes por producto')
    pdf.parrafo('Tabla con el margen de ganancia de cada producto activo, ordenados de mayor a menor margen.')

    pdf.nota('Tip: si el margen de un producto es negativo, revisa si el costo esta actualizado en el catalogo de Productos.', 'warning')

    # ══════════════════════════════════════════════════════════════════════════
    # 12. DESPACHO
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('12', 'Modulo Despacho')

    pdf.parrafo('Gestiona las entregas a domicilio y el seguimiento de pedidos en camino.')

    pdf.subtitulo('Estados de despacho')
    pdf.tabla_simple(
        ['Estado', 'Significado'],
        [
            ['Pendiente', 'Pedido recibido, aun no se prepara'],
            ['En preparacion', 'Se esta empacando o preparando para salir'],
            ['Despachado', 'Ya salio a entregarse'],
            ['Entregado', 'Cliente confirmo recepcion'],
            ['Retiro en Tienda', 'El cliente retira personalmente'],
        ],
        [50, 120]
    )

    pdf.subtitulo('Flujo de un despacho')
    pdf.paso(1, 'Se crea la venta con fecha de despacho y "Con despacho: Si".')
    pdf.paso(2, 'Aparece en Despacho con estado "Pendiente".')
    pdf.paso(3, 'Al preparar el pedido, cambiar a "En preparacion".')
    pdf.paso(4, 'Cuando el repartidor sale, cambiar a "Despachado".')
    pdf.paso(5, 'Al recibir confirmacion del cliente, cambiar a "Entregado".')
    pdf.ln(3)

    pdf.subtitulo('Filtros')
    pdf.parrafo('Puedes filtrar por rango de fechas y por estado para ver solo los pendientes del dia o semana.')

    # ══════════════════════════════════════════════════════════════════════════
    # 13. SUSCRIPCIONES
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('13', 'Modulo Suscripciones')

    pdf.parrafo('Administra los planes de entrega recurrente para clientes fijos.')

    pdf.subtitulo('Crear una suscripcion')
    pdf.tabla_simple(
        ['Campo', 'Descripcion', 'Ejemplo'],
        [
            ['Cliente', 'Quien recibe', 'Cafe El Bosque'],
            ['Productos', 'Que recibe cada vez', '10 Pan Molde Blanco'],
            ['Plan', 'Frecuencia', 'Semanal / Quincenal / Mensual'],
            ['Precio', 'Valor de cada entrega', '$42.000'],
            ['Dia de despacho', 'Que dia se entrega', 'Lunes'],
            ['Fecha inicio', 'Cuando comienza', '2026-05-05'],
        ],
        [38, 75, 57]
    )

    pdf.subtitulo('Ciclos de entrega')
    pdf.parrafo('Cada suscripcion tiene un contador de entregas (0 a 4 puntos visuales). '
                'Al registrar una entrega, el contador avanza. Cuando llega a 4, se renueva automaticamente el ciclo.')

    pdf.subtitulo('Registrar una entrega')
    pdf.paso(1, 'Busca la suscripcion del cliente en la lista.')
    pdf.paso(2, 'Haz clic en "Registrar entrega" (icono camion).')
    pdf.paso(3, 'El sistema crea una venta automaticamente con los productos de la suscripcion.')
    pdf.paso(4, 'El contador de entregas avanza un punto.')
    pdf.ln(3)

    pdf.nota('Las suscripciones proximas a renovarse (ciclo completo) aparecen con una alerta en la parte superior.', 'tip')

    # ══════════════════════════════════════════════════════════════════════════
    # 14. REPORTES
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('14', 'Modulo Reportes y Analisis')

    pdf.subtitulo('Reportes — Graficos Visuales')
    pdf.parrafo('Vista grafica de las ventas con KPIs principales:')
    pdf.bullet('Total de ventas en el periodo seleccionado')
    pdf.bullet('Numero de pedidos y ticket promedio')
    pdf.bullet('Grafico de linea: ventas por dia')
    pdf.bullet('Grafico de barras: productos mas vendidos')
    pdf.bullet('Grafico de torta: ventas por canal (local, online, suscripcion)')

    pdf.parrafo('Periodos disponibles: ultima semana, ultimo mes, ultimos 3 meses, ultimo ano.')

    pdf.subtitulo('Reporte de Ventas — Tabla Detallada')
    pdf.parrafo('Vista tabular con drill-down:')
    pdf.bullet('Resumen mensual con totales')
    pdf.bullet('Clic en un mes para ver desglose semanal')
    pdf.bullet('Filtros por canal, tipo de cliente, estado de pago y cliente especifico')
    pdf.bullet('Tabla detalle con cada venta: fecha, cliente, canal, productos, monto, estado')

    pdf.nota('Para exportar datos, usa los filtros para mostrar lo que necesitas y luego usa la funcion de impresion del navegador (Ctrl+P) o guarda como PDF.', 'info')

    # ══════════════════════════════════════════════════════════════════════════
    # 15. CRM
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('15', 'Modulo CRM — Captacion de Clientes')

    pdf.parrafo('El CRM ayuda a gestionar el proceso de captacion de nuevos clientes B2B (cafes, restaurantes, hoteles) y B2C (clientes individuales).')

    pdf.subtitulo('Pipeline de ventas (Kanban)')
    pdf.tabla_simple(
        ['Etapa', 'Descripcion'],
        [
            ['Prospecto', 'Cliente potencial identificado, aun no contactado'],
            ['Contactado', 'Primer contacto realizado'],
            ['Negociando', 'En conversacion activa sobre precios y condiciones'],
            ['Propuesta enviada', 'Se envio cotizacion o muestra'],
            ['Ganado', 'Se cerro la venta, es cliente'],
            ['Perdido', 'No prosiguio, descartado por ahora'],
        ],
        [50, 120]
    )

    pdf.subtitulo('Buscar leads con IA')
    pdf.parrafo('El modulo CRM incluye una herramienta de busqueda de prospectos usando Google Places. '
                'Busca "cafes en Providencia" y el sistema encuentra negocios con nombre, direccion y telefono.')

    pdf.subtitulo('Comunicaciones masivas')
    pdf.bullet('Email masivo: envia campanas a todos los leads o segmentos.')
    pdf.bullet('WhatsApp masivo: envia mensajes por WhatsApp a multiples leads.')

    # ══════════════════════════════════════════════════════════════════════════
    # 16. DASHBOARD MOVIL
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('16', 'Dashboard Movil')

    pdf.parrafo('Vista resumida optimizada para celular. Accede desde cualquier dispositivo para monitorear el negocio en tiempo real.')

    pdf.subtitulo('KPIs disponibles')
    pdf.tabla_simple(
        ['Indicador', 'Descripcion'],
        [
            ['Ventas de hoy', 'Suma de ventas del dia actual'],
            ['Ventas semana', 'Suma de ventas de la semana en curso'],
            ['Ventas mes', 'Suma de ventas del mes en curso'],
            ['Margen del mes', 'Porcentaje de ganancia sobre ventas'],
            ['Gastos del mes', 'Total de egresos registrados este mes'],
            ['Despachos pendientes', 'Pedidos aun no entregados hoy'],
        ],
        [55, 115]
    )

    pdf.subtitulo('Stock critico')
    pdf.parrafo('Muestra los productos con stock igual o menor a 5 unidades para actuar rapido antes de quedarse sin stock.')

    pdf.subtitulo('Top productos')
    pdf.parrafo('Los 5 productos mas vendidos de la semana en curso con unidades vendidas e ingresos.')

    pdf.subtitulo('Instalarlo como app en el celular (PWA)')
    pdf.paso(1, 'Abre el sistema en el navegador de tu celular.')
    pdf.paso(2, 'En Safari (iPhone): toca el boton Compartir y selecciona "Agregar a pantalla de inicio".')
    pdf.paso(3, 'En Chrome (Android): toca el menu (3 puntos) y selecciona "Instalar aplicacion".')
    pdf.paso(4, 'Aparecera un icono de Aurora Bakers en tu pantalla de inicio como una app normal.')
    pdf.ln(3)
    pdf.nota('La app funciona sin conexion para las pantallas ya visitadas. Los datos siempre son en tiempo real cuando hay internet.', 'info')

    # ══════════════════════════════════════════════════════════════════════════
    # 17. AGENDA
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('17', 'Agenda')

    pdf.parrafo('Planifica tareas, eventos y reuniones con un calendario tipo Gantt.')

    pdf.subtitulo('Tipos de elemento')
    pdf.tabla_simple(
        ['Tipo', 'Ejemplo de uso'],
        [
            ['Tarea', 'Limpiar horno, revisar inventario, llamar proveedor'],
            ['Evento', 'Feria gastronómica, reunion con socio'],
            ['Reunion', 'Junta de equipo, entrevista de trabajo'],
            ['Entrega', 'Despacho especial fuera del flujo normal'],
        ],
        [40, 130]
    )

    pdf.subtitulo('Vista Gantt')
    pdf.parrafo('El calendario muestra barras horizontales para cada tarea con sus fechas de inicio y fin. '
                'Los colores indican prioridad: rojo (alta), naranja (media), gris (baja).')
    pdf.parrafo('Puedes ver por semana, dos semanas o mes completo. Haz clic en cualquier barra para editar.')

    # ══════════════════════════════════════════════════════════════════════════
    # 18. USUARIOS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('18', 'Gestion de Usuarios')

    pdf.parrafo('Solo los administradores pueden crear, editar y desactivar usuarios.')

    pdf.subtitulo('Crear un usuario')
    pdf.paso(1, 'Ir a Usuarios en el menu lateral.')
    pdf.paso(2, 'Clic en "Nuevo usuario".')
    pdf.paso(3, 'Ingresar nombre, email y contrasena.')
    pdf.paso(4, 'Seleccionar rol: Administrador (todo acceso) o Usuario (acceso limitado).')
    pdf.paso(5, 'Si el rol es Usuario, marcar los modulos a los que puede acceder.')
    pdf.paso(6, 'Guardar.')
    pdf.ln(3)

    pdf.subtitulo('Permisos por modulo')
    pdf.parrafo('Puedes dar acceso solo a los modulos que cada persona necesita:')
    pdf.tabla_simple(
        ['Perfil tipico', 'Modulos recomendados'],
        [
            ['Cajero', 'POS, Ventas, Despacho'],
            ['Panadero', 'Produccion, Inventario'],
            ['Repartidor', 'Despacho'],
            ['Vendedor', 'Ventas, Clientes, Suscripciones, CRM, Agenda'],
            ['Contador', 'Gastos, Finanzas, Reportes'],
            ['Administrador', 'Todos los modulos'],
        ],
        [45, 125]
    )

    pdf.subtitulo('Cambiar contrasena')
    pdf.parrafo('Cada usuario puede cambiar su propia contrasena desde el panel de Usuarios. '
                'El administrador puede resetear la contrasena de cualquier usuario.')

    # ══════════════════════════════════════════════════════════════════════════
    # 19. FLUJOS COMPLETOS CON EJEMPLOS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('19', 'Flujos Completos con Ejemplos')

    # FLUJO 1: VENTA EN MOSTRADOR
    pdf.subtitulo('Flujo 1: Venta en mostrador con efectivo')
    pdf.parrafo('Cliente llega a comprar 3 hogazas y 2 ciabattas.')
    pdf.tabla_simple(
        ['Paso', 'Donde', 'Que hacer'],
        [
            ['1', 'POS / Caja', 'Verificar que el turno esta abierto'],
            ['2', 'POS', 'Clic en "Hogaza Campesina", ajustar cantidad a 3'],
            ['3', 'POS', 'Clic en "Ciabatta", ajustar cantidad a 2'],
            ['4', 'POS', 'Total: $13.800. Seleccionar "Efectivo"'],
            ['5', 'POS', 'Cliente paga $15.000. Vuelto: $1.200'],
            ['6', 'POS', 'Clic "Cobrar" — venta registrada, stock descontado'],
        ],
        [10, 35, 125]
    )

    # FLUJO 2: PEDIDO CON DESPACHO
    pdf.subtitulo('Flujo 2: Pedido de cliente con despacho al dia siguiente')
    pdf.parrafo('Cafe El Bosque llama y pide 20 panes molde para manana.')
    pdf.tabla_simple(
        ['Paso', 'Donde', 'Que hacer'],
        [
            ['1', 'Ventas', 'Clic "Nueva venta"'],
            ['2', 'Ventas', 'Seleccionar cliente "Cafe El Bosque", tipo Mayorista'],
            ['3', 'Ventas', 'Agregar producto: Pan Molde Blanco x20 (precio mayorista)'],
            ['4', 'Ventas', 'Canal: Online. Con despacho: Si. Fecha despacho: manana'],
            ['5', 'Ventas', 'Estado pago: Pendiente. Guardar'],
            ['6', 'Produccion', 'Plan del dia siguiente ya muestra el pedido automaticamente'],
            ['7', 'Produccion', 'Confirmar produccion: stock de Pan Molde sube +20'],
            ['8', 'Despacho', 'Cambiar estado a "En preparacion", luego "Despachado"'],
            ['9', 'Finanzas', 'Cuando el cliente pague, marcar como "Pagado"'],
        ],
        [10, 35, 125]
    )

    # FLUJO 3: SUSCRIPCION
    pdf.add_page()
    pdf.subtitulo('Flujo 3: Configurar y gestionar una suscripcion semanal')
    pdf.parrafo('Nuevo cliente corporativo quiere recibir pan todos los lunes.')
    pdf.tabla_simple(
        ['Paso', 'Donde', 'Que hacer'],
        [
            ['1', 'Clientes', 'Crear cliente "Hotel Andino" como Mayorista'],
            ['2', 'Suscripciones', 'Clic "Nueva suscripcion"'],
            ['3', 'Suscripciones', 'Cliente: Hotel Andino, Plan: Semanal, Dia: Lunes'],
            ['4', 'Suscripciones', 'Productos: 30 Ciabatta + 10 Pan Molde. Precio: $62.000'],
            ['5', 'Suscripciones', 'Guardar suscripcion'],
            ['6', 'Cada lunes', 'En Suscripciones, clic "Registrar entrega" en Hotel Andino'],
            ['7', 'Auto', 'El sistema crea la venta y avanza el contador de entregas'],
            ['8', 'Finanzas', 'El cobro aparece en pendientes hasta que paguen'],
        ],
        [10, 35, 125]
    )

    # FLUJO 4: CIERRE DE MES
    pdf.subtitulo('Flujo 4: Cierre del mes')
    pdf.tabla_simple(
        ['Paso', 'Donde', 'Que hacer'],
        [
            ['1', 'Gastos', 'Clic "Gastos Fijos" > "Aplicar al mes actual" para registrar arriendos y servicios'],
            ['2', 'Gastos', 'Revisar si falta agregar algun gasto variable del mes'],
            ['3', 'Finanzas', 'Revisar cobros pendientes y gestionar los no pagados'],
            ['4', 'Finanzas', 'Ver el P&L del mes: ingresos vs egresos vs ganancia'],
            ['5', 'Reportes', 'Revisar productos mas vendidos y margenes'],
            ['6', 'Inventario', 'Hacer conteo fisico y ajustar stocks si es necesario'],
            ['7', 'Productos', 'Revisar margenes, actualizar costos si cambiaron los insumos'],
        ],
        [10, 35, 125]
    )

    # FLUJO 5: PRODUCCION COMPLETA
    pdf.subtitulo('Flujo 5: Dia completo de produccion con ingredientes')
    pdf.parrafo('Dia con fichas tecnicas configuradas y descuento de inventario automatico.')
    pdf.tabla_simple(
        ['Paso', 'Donde', 'Resultado'],
        [
            ['1', 'Produccion', 'Plan: 20 Hogazas + 15 Pan Molde + 40 Ciabatta'],
            ['2', 'Produccion', 'Clic "Confirmar Produccion"'],
            ['3', 'Auto — Productos', 'Stock: +20 Hogazas, +15 Pan Molde, +40 Ciabatta'],
            ['4', 'Auto — Inventario', 'Se descuenta harina, agua, sal, levadura segun recetas'],
            ['5', 'Inventario', 'Alerta: Levadura bajo minimo (quedaron 0.3 kg de 0.5 min)'],
            ['6', 'Gastos', 'Registrar compra de insumos si fue necesario reponer'],
        ],
        [10, 35, 125]
    )

    # ══════════════════════════════════════════════════════════════════════════
    # 20. PREGUNTAS FRECUENTES
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.titulo_seccion('20', 'Preguntas Frecuentes')

    faqs = [
        ('El stock de un producto quedo en negativo. Que hago?',
         'Registra la produccion del dia en el modulo Produccion y confirma la hornada. '
         'El sistema sumara el stock producido. Si hay error historico, puedes ajustar el stock '
         'manualmente desde el modulo Productos editando el producto.'),
        ('Olvide registrar un gasto del mes pasado. Puedo igual ingresarlo?',
         'Si. Al crear un gasto puedes cambiar la fecha manualmente a cualquier fecha pasada. '
         'El sistema lo registrara en el mes que corresponde para el P&L.'),
        ('Como cambio el precio de un producto?',
         'Ve a Productos, haz clic en el boton de editar (lapiz) del producto y cambia el precio. '
         'Los cambios aplican a las nuevas ventas; las ventas pasadas conservan el precio original.'),
        ('Un cajero puede ver los reportes financieros?',
         'Solo si el administrador le da permiso al modulo Finanzas o Reportes. '
         'Por defecto un cajero solo ve POS, Ventas y Despacho.'),
        ('El sistema funciona sin internet?',
         'Si esta instalado como PWA en el celular, las pantallas visitadas recientemente '
         'funcionan sin conexion. Pero para registrar ventas o ver datos nuevos necesitas internet.'),
        ('Puedo tener mas de un turno de caja abierto simultaneamente?',
         'No. Solo puede haber un turno activo a la vez. Cierra el turno anterior antes de abrir uno nuevo.'),
        ('Como saco un reporte de ventas por cliente?',
         'Ve a Reporte de Ventas, aplica el filtro "Cliente" y selecciona al cliente. '
         'Veras todas sus compras en el periodo seleccionado.'),
        ('Se puede integrar con Bsale para emitir boletas electronicas?',
         'Si. En Configuracion ingresa las credenciales de Bsale (token, tipo de documento). '
         'Desde ese momento el POS emitira boletas automaticamente en cada venta.'),
        ('Como ingreso una devolucion o nota de credito?',
         'Crea una venta con monto negativo o usa el boton de eliminar la venta original. '
         'Esto restaurara el stock de los productos involucrados.'),
        ('El sistema tiene respaldo de datos?',
         'El sistema en Railway hace respaldos automaticos. Para instalaciones locales, '
         'copia periodicamente el archivo aurora.db que contiene toda la base de datos.'),
    ]

    for pregunta, respuesta in faqs:
        pdf.set_font('Arial', 'B', 10)
        pdf.set_text_color(*C_AURORA)
        pdf.multi_cell(170, 6, f'P: {pregunta}')
        pdf.set_font('Arial', '', 10)
        pdf.set_text_color(*C_TEXT)
        pdf.set_x(25)
        pdf.multi_cell(165, 5.5, f'R: {respuesta}')
        pdf.linea_divisoria()

    # ── CONTRAPORTADA ──────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_fill_color(*C_DARK)
    pdf.rect(0, 0, 210, 297, 'F')
    pdf.set_y(120)
    pdf.set_font('Arial', 'B', 18)
    pdf.set_text_color(*C_AURORA)
    pdf.cell(0, 10, 'Aurora Bakers', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Arial', '', 11)
    pdf.set_text_color(200, 200, 200)
    pdf.cell(0, 8, 'Sistema de Gestion Integral', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(10)
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(0, 6, f'Generado el {date.today().strftime("%d/%m/%Y")}', align='C',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, 'Contacto: nwalbaum@panypasta.cl', align='C')

    return pdf


if __name__ == '__main__':
    pdf = build_pdf()
    out = 'manual_aurora_bakers.pdf'
    pdf.output(out)
    print(f'Manual generado: {out}  ({pdf.page_no()} paginas)')
