import os
import json
import psycopg2
import urllib.parse
from datetime import datetime
from flask import Flask, request, jsonify
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_db_connection():
    """Подключение к базе данных"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL not set")
            return None
        
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        conn = psycopg2.connect(database_url, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"❌ DB connection error: {e}")
        return None

def init_database():
    """Инициализация таблиц базы данных с исправлением структуры"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # 1. Основная таблица сигналов
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trading_signals (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(50) NOT NULL,
                signal VARCHAR(50) NOT NULL,
                price DECIMAL(20, 8) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # 2. Расширенная таблица KIRA
        cur.execute('''
            CREATE TABLE IF NOT EXISTS kiria_full_signals (
                id SERIAL PRIMARY KEY,
                signal_id INTEGER REFERENCES trading_signals(id) ON DELETE CASCADE,
                full_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. Добавляем колонку source, если её нет (исправление ошибки)
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trading_signals' AND column_name='source'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE trading_signals ADD COLUMN source VARCHAR(50) DEFAULT 'unknown'")
                logger.info("✅ Добавлена колонка 'source' в таблицу trading_signals")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось добавить колонку source: {e}")
        
        # 4. Создаём индексы
        cur.execute('CREATE INDEX IF NOT EXISTS idx_kiria_signal_id ON kiria_full_signals(signal_id)')
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Таблицы базы данных инициализированы")
        return True
    except Exception as e:
        logger.error(f"❌ DB init error: {e}")
        return False

init_database()

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "status": "running",
        "version": "3.1 (Fixed Structure)",
        "webhook_url": "https://tradingview-proxy-h71n.onrender.com/webhook",
        "endpoints": {
            "health": "/health",
            "webhook": "/webhook (POST/GET)",
            "signals": "/signals (GET)",
            "kiria_signals": "/kiria/signals (GET)",
            "fix_database": "/fix_db (GET) - исправить структуру БД"
        }
    })

@app.route('/health')
def health():
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM trading_signals")
            trading_count = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM kiria_full_signals")
            kiria_count = cur.fetchone()[0]
            
            # Проверяем структуру таблицы
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'trading_signals'
                ORDER BY ordinal_position
            """)
            columns = [{"name": row[0], "type": row[1]} for row in cur.fetchall()]
            
            cur.close()
            conn.close()
            
            db_status = {
                "trading_signals": trading_count,
                "kiria_full_signals": kiria_count,
                "columns": columns,
                "status": "connected"
            }
        else:
            db_status = {"status": "disconnected"}
            
        return jsonify({
            "status": "healthy",
            "database": db_status,
            "timestamp": datetime.now().isoformat(),
            "version": "3.1"
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/fix_db')
def fix_database():
    """Ручное исправление структуры базы данных"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
        # Добавляем колонку source если её нет
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trading_signals' AND column_name='source'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE trading_signals ADD COLUMN source VARCHAR(50) DEFAULT 'unknown'")
                logger.info("✅ Колонка 'source' добавлена")
            else:
                logger.info("✅ Колонка 'source' уже существует")
        except Exception as e:
            logger.error(f"❌ Ошибка добавления колонки: {e}")
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Database structure fixed",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    """Универсальный вебхук для TradingView"""
    try:
        data = {}
        
        # 🔥 ПРИНИМАЕМ ВСЁ что угодно
        
        # 1. JSON
        if request.is_json:
            try:
                data = request.get_json()
                logger.info("✅ Получен JSON")
            except:
                pass
        
        # 2. Raw text (TradingView часто так отправляет)
        if not data and request.data:
            try:
                raw_text = request.data.decode('utf-8')
                logger.info(f"📝 Raw данные: {raw_text[:200]}...")
                
                # Пробуем распарсить как JSON
                if raw_text.strip().startswith('{'):
                    try:
                        data = json.loads(raw_text)
                    except:
                        # Может быть JSON в кавычках
                        if raw_text.startswith('"') and raw_text.endswith('"'):
                            data = json.loads(raw_text[1:-1])
                        else:
                            data = {'raw': raw_text}
                else:
                    data = {'raw': raw_text}
                    
            except Exception as e:
                logger.error(f"❌ Ошибка обработки raw: {e}")
                data = {'error': str(e)}
        
        # 3. Form-data
        if not data and request.form:
            data = request.form.to_dict()
        
        # 4. GET параметры
        if not data and request.args:
            data = request.args.to_dict()
        
        # Если данных нет
        if not data:
            return jsonify({
                "status": "warning",
                "message": "No data received",
                "tip": "Send JSON like: {\"symbol\":\"BTC\",\"signal\":\"BUY\",\"price\":50000}"
            }), 200
        
        logger.info(f"📥 Получены данные: {json.dumps(data, ensure_ascii=False)[:500]}")
        
        # Извлекаем основные поля
        symbol = str(data.get('symbol') or data.get('ticker') or 'UNKNOWN').upper()[:50]
        
        # Сигнал может быть в разных полях
        signal_raw = data.get('signal') or data.get('action') or data.get('order') or 'UNKNOWN'
        signal = str(signal_raw)[:50].upper()
        
        # Цена
        price_str = data.get('price') or data.get('close') or '0'
        try:
            price = float(price_str)
        except:
            price = 0.0
        
        # Сохраняем в базу
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
        try:
            # Вставляем в trading_signals (без source сначала)
            cur.execute('''
                INSERT INTO trading_signals (symbol, signal, price)
                VALUES (%s, %s, %s)
                RETURNING id, timestamp
            ''', (symbol, signal, price))
            
            signal_id, timestamp = cur.fetchone()
            
            # Сохраняем все данные в KIRA таблицу
            cur.execute('''
                INSERT INTO kiria_full_signals (signal_id, full_data)
                VALUES (%s, %s)
            ''', (signal_id, json.dumps(data)))
            
            conn.commit()
            
            logger.info(f"✅ Сигнал сохранен: {symbol} {signal} ${price:.2f} (ID: {signal_id})")
            
            # Проверяем KIRA данные
            kira_keys = ['monitoring_minutes', 'delta_15min', 'bull_percent', 'dominance']
            has_kira = any(key in data for key in kira_keys)
            
        except Exception as db_error:
            conn.rollback()
            logger.error(f"❌ Ошибка базы данных: {db_error}")
            return jsonify({"error": f"Database error: {db_error}"}), 500
        finally:
            cur.close()
            conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Signal received and saved",
            "signal_id": signal_id,
            "data": {
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "timestamp": timestamp.isoformat() if timestamp else None,
                "has_kira_data": has_kira
            }
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка вебхука: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/signals')
def get_signals():
    """Получение всех сигналов (исправленная версия без source)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        limit = min(int(request.args.get('limit', 50)), 1000)
        offset = int(request.args.get('offset', 0))
        
        cur = conn.cursor()
        
        # Проверяем есть ли колонка source
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='trading_signals' AND column_name='source'
        """)
        has_source = cur.fetchone() is not None
        
        # Формируем запрос в зависимости от структуры таблицы
        if has_source:
            cur.execute('''
                SELECT id, symbol, signal, price, timestamp, source 
                FROM trading_signals 
                ORDER BY timestamp DESC 
                LIMIT %s OFFSET %s
            ''', (limit, offset))
            
            signals = []
            for row in cur.fetchall():
                signals.append({
                    "id": row[0],
                    "symbol": row[1],
                    "signal": row[2],
                    "price": float(row[3]),
                    "timestamp": row[4].isoformat() if row[4] else None,
                    "source": row[5]
                })
        else:
            cur.execute('''
                SELECT id, symbol, signal, price, timestamp 
                FROM trading_signals 
                ORDER BY timestamp DESC 
                LIMIT %s OFFSET %s
            ''', (limit, offset))
            
            signals = []
            for row in cur.fetchall():
                signals.append({
                    "id": row[0],
                    "symbol": row[1],
                    "signal": row[2],
                    "price": float(row[3]),
                    "timestamp": row[4].isoformat() if row[4] else None
                })
        
        cur.execute("SELECT COUNT(*) FROM trading_signals")
        total = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "total": total,
            "has_source_column": has_source,
            "signals": signals
        })
    except Exception as e:
        logger.error(f"Error in get_signals: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/kiria/signals')
def get_kiria_signals():
    """Получение KIRA сигналов"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        limit = min(int(request.args.get('limit', 50)), 1000)
        
        cur = conn.cursor()
        
        # Проверяем существование таблицы
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kiria_full_signals')")
        if not cur.fetchone()[0]:
            cur.close()
            conn.close()
            return jsonify({
                "status": "success",
                "message": "KIRA table not created yet",
                "signals": []
            })
        
        cur.execute('''
            SELECT 
                ts.id,
                ts.symbol,
                ts.signal,
                ts.price,
                ts.timestamp,
                kfs.full_data
            FROM trading_signals ts
            LEFT JOIN kiria_full_signals kfs ON ts.id = kfs.signal_id
            WHERE kfs.full_data IS NOT NULL
            ORDER BY ts.timestamp DESC
            LIMIT %s
        ''', (limit,))
        
        signals = []
        for row in cur.fetchall():
            try:
                full_data = json.loads(row[5]) if isinstance(row[5], str) else row[5]
            except:
                full_data = {}
            
            signals.append({
                "id": row[0],
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat() if row[4] else None,
                "kira_data": {
                    "monitoring_minutes": full_data.get('monitoring_minutes', 0),
                    "delta_15min": full_data.get('delta_15min', 0),
                    "bull_percent": full_data.get('bull_percent', 50),
                    "dominance": full_data.get('dominance', 'NEUTRAL')
                }
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "signals": signals
        })
    except Exception as e:
        logger.error(f"Error in get_kiria_signals: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Исправленный сервер запущен на порту {port}")
    logger.info(f"🌐 URL: https://tradingview-proxy-h71n.onrender.com")
    logger.info(f"✅ Вебхук: https://tradingview-proxy-h71n.onrender.com/webhook")
    logger.info(f"🛠️  Чтобы исправить структуру БД открой: /fix_db")
    app.run(host='0.0.0.0', port=port, debug=False)
