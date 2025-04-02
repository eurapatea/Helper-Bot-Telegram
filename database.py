import psycopg2
from dotenv import load_dotenv
import os

# Загружаем переменные из .env
load_dotenv()

# Параметры подключения к PostgreSQL
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")
DB_PASS = os.getenv("DB_PASS")

def init_db():
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        # Создание таблицы tickets
        c.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                config TEXT,
                org_dept TEXT,
                name TEXT,
                phone TEXT,
                description TEXT,
                status TEXT DEFAULT 'Принято'
            )
        ''')
        # Создание таблицы admins
        c.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY
            )
        ''')
        # Создание таблицы feedback
        c.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                ticket_id INTEGER PRIMARY KEY,
                rating INTEGER,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            )
        ''')
        conn.commit()
        print("База данных успешно инициализирована!")
    except Exception as e:
        print(f"Ошибка инициализации базы данных: {e}")
    finally:
        if conn:
            conn.close()

def add_admin(user_id):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        conn.commit()
    except Exception as e:
        print(f"Ошибка добавления администратора: {e}")
    finally:
        if conn:
            conn.close()

def is_admin(user_id):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        return result is not None
    except Exception as e:
        print(f"Ошибка проверки администратора: {e}")
        return False
    finally:
        if conn:
            conn.close()

def save_ticket(user_id, config, org_dept, name, phone, description):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute(
            "INSERT INTO tickets (user_id, config, org_dept, name, phone, description) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, config, org_dept, name, phone, description)
        )
        ticket_id = c.fetchone()[0]
        conn.commit()
        return ticket_id
    except Exception as e:
        print(f"Ошибка сохранения заявки: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_status(ticket_id, status):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("UPDATE tickets SET status = %s WHERE id = %s", (status, ticket_id))
        conn.commit()
    except Exception as e:
        print(f"Ошибка обновления статуса: {e}")
    finally:
        if conn:
            conn.close()

def get_user_id_by_ticket(ticket_id):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("SELECT user_id FROM tickets WHERE id = %s", (ticket_id,))
        result = c.fetchone()
        return result[0] if result else None
    except Exception as e:
        print(f"Ошибка получения user_id: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_tickets_by_status(status):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE status = %s", (status,))
        result = c.fetchall()
        return result
    except Exception as e:
        print(f"Ошибка получения заявок: {e}")
        return []
    finally:
        if conn:
            conn.close()

def save_feedback(ticket_id, rating):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("INSERT INTO feedback (ticket_id, rating) VALUES (%s, %s) ON CONFLICT (ticket_id) DO UPDATE SET rating = %s",
                  (ticket_id, rating, rating))
        conn.commit()
    except Exception as e:
        print(f"Ошибка сохранения отзыва: {e}")
    finally:
        if conn:
            conn.close()

def get_feedback(ticket_id):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        c = conn.cursor()
        c.execute("SELECT rating FROM feedback WHERE ticket_id = %s", (ticket_id,))
        result = c.fetchone()
        return result[0] if result else None
    except Exception as e:
        print(f"Ошибка получения отзыва: {e}")
        return None
    finally:
        if conn:
            conn.close()