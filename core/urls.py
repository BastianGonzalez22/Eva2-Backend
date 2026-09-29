"""
RUTAS PRINCIPALES DEL PROYECTO (core/urls.py)
Incluye endpoints de la API REST, Swagger OpenAPI, autenticación JWT,
vistas web y captura 404 controlada mediante re_path.
"""

from django.urls import path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView
from transporte import views, api_views

urlpatterns = [
    # Documentación Swagger / OpenAPI
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # Autenticación JWT (con claim personalizado de ROL)
    path('api/token/', api_views.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Endpoints Públicos de Consulta
    path('api/servicios/buscar/', api_views.BuscarServiciosAPI.as_view(), name='api_servicios_buscar'),
    path('api/servicios/<int:servicio_id>/asientos/', api_views.AsientosServicioAPI.as_view(), name='api_servicios_asientos'),

    # Endpoints de Pasajero Autenticado
    path('api/carro-pasajes/', api_views.CarroPasajesAPI.as_view(), name='api_carro_pasajes'),
    path('api/ventas/checkout/', api_views.CheckoutAPI.as_view(), name='api_ventas_checkout'),
    path('api/mis-boletos/', api_views.MisBoletosAPI.as_view(), name='api_mis_boletos'),

    # Endpoints Administrativos (ADMIN_FLOTA)
    path('api/servicios/', api_views.GestionServiciosAPI.as_view(), name='api_gestion_servicios'),
    path('api/servicios/<int:pk>/', api_views.GestionServiciosDetalleAPI.as_view(), name='api_gestion_servicios_detalle'),
    path('api/ventas/<int:pk>/estado/', api_views.CambiarEstadoVentaAPI.as_view(), name='api_cambiar_estado_venta'),

    # Vistas Web (Frontend Django Templates)
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('registro/', views.registro_view, name='registro'),
    path('servicio/<int:servicio_id>/', views.detalle_servicio, name='detalle_servicio'),
    path('carro/', views.ver_carro, name='ver_carro'),
    path('carro/eliminar/<int:item_id>/', views.eliminar_item_carro, name='eliminar_item_carro'),
    path('carro/checkout/', views.procesar_checkout, name='procesar_checkout'),
    path('comprobante/<int:boleto_id>/', views.comprobante, name='comprobante'),
    path('gestion/', views.gestion_servicios, name='gestion_servicios'),

    # Manejo de error 404 controlado exigido por acta (funciona con DEBUG = True y False)
    re_path(r'^(?!api/|static/).+$', views.error_404_view, name='control_404'),
]

handler404 = 'transporte.views.error_404_view'