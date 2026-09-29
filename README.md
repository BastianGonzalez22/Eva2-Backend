# AndesSur - Sistema de Venta de Pasajes Interurbanos

**Estudiante:** Bastián González  
**Carrera:** Analista Programador  
**Sección:** N3-C2 • Año 2026  

---

## 1. Configuración de Base de Datos en PostgreSQL

Antes de iniciar el servidor, cree la base de datos y el usuario configurados en `core/settings.py`:

```sql
CREATE DATABASE transporte_db;
CREATE USER bastian WITH PASSWORD '123456';
GRANT ALL PRIVILEGES ON DATABASE transporte_db TO bastian;
ALTER DATABASE transporte_db OWNER TO bastian;
```

---

## 2. Instalación y Despliegue

1. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

2. Aplicar migraciones:
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

3. Poblar datos iniciales de prueba:
   ```bash
   python poblar.py
   ```

4. Iniciar el servidor local:
   ```bash
   python manage.py runserver
   ```

---

## 3. Credenciales de Prueba

- **Administrador de Flota:** `admin` / `admin123`
- **Pasajero Cliente:** `pasajero` / `pasajero123`

---

## 4. Documentación y Rutas Principales

- **Frontend Web:** `http://localhost:8000/`
- **Documentación OpenAPI / Swagger:** `http://localhost:8000/api/docs/`
- **Autenticación JWT:** `http://localhost:8000/api/token/`