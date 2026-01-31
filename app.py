import os
import json
import psycopg2
from datetime import datetime
from flask import Flask, request, jsonify
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL not set")
        return None
    try:
        # Исправляем URL для psycopg2
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(database_url, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None

def init_database():
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # 1. Существующая таблица для совместимости
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trading_signals (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                signal VARCHAR(10) NOT NULL,
                price DECIMAL(15, 5) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # 2. НОВАЯ таблица для расширенных данных KIRA
        cur.execute('''
            CREATE TABLE IF NOT EXISTS kiria_full_signals (
                id SERIAL PRIMARY KEY,
                signal_id INTEGER REFERENCES trading_signals(id),
                full_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. Индекс для быстрого поиска
        cur.execute('CREATE INDEX IF NOT EXISTS idx_kiria_signal_id ON kiria_full_signals(signal_id)')
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database tables initialized (including KIRA tables)")
        return True
    except Exception as e:
        logger.error(f"DB init error: {e}")
        return False

init_database()

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "status": "running",
        "version": "2.0 (with KIRA support)",
        "webhook_url": "https://tradingview-proxy-h71n.onrender.com/webhook",
        "endpoints": {
            "health": "/health",
            "webhook": "/webhook (POST)",
            "signals": "/signals (GET)",
            "active_signals": "/signals/active (GET)",
            "kiria_signals": "/kiria/signals (GET)",  # НОВЫЙ
            "kiria_signal_by_id": "/kiria/signal/<id> (GET)"  # НОВЫЙ
        }
    })

@app.route('/health')
def health():
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            # Проверяем обе таблицы
            cur.execute("SELECT COUNT(*) FROM trading_signals")
            trading_count = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM kiria_full_signals")
            kiria_count = cur.fetchone()[0]
            
            cur.close()
            conn.close()
            
            db_status = {
                "trading_signals": trading_count,
                "kiria_full_signals": kiria_count,
                "status": "connected"
            }
        else:
            db_status = {"status": "disconnected"}
            
        return jsonify({
            "status": "healthy",
            "database": db_status,
            "timestamp": datetime.now().isoformat(),
            "version": "2.0"
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No JSON data"}), 400
        
        # Основные обязательные поля
        symbol = data.get('symbol', '').upper()
        signal = data.get('signal', '').upper()
        
        try:
            price = float(data.get('price', 0))
        except:
            return jsonify({"error": "Invalid price"}), 400
        
        if not symbol or not signal or price <= 0:
            return jsonify({"error": "Missing required fields"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
        # 1. Сохраняем в основную таблицу (для совместимости)
        cur.execute('''
            INSERT INTO trading_signals (symbol, signal, price)
            VALUES (%s, %s, %s)
            RETURNING id, timestamp
        ''', (symbol, signal, price))
        
        signal_id, timestamp = cur.fetchone()
        
        # 2. Сохраняем ВСЕ данные в KIRA таблицу
        cur.execute('''
            INSERT INTO kiria_full_signals (signal_id, full_data)
            VALUES (%s, %s)
        ''', (signal_id, json.dumps(data)))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Логируем расширенные данные, если они есть
        has_kiria_data = any(key in data for key in [
            'monitoring_minutes', 'delta_15min', 
            'total_delta_90min', 'bull_percent', 'dominance'
        ])
        
        logger.info(f"📥 Signal saved: {symbol} {signal} (ID: {signal_id})")
        if has_kiria_data:
            logger.info(f"   📊 KIRA data included: monitoring={data.get('monitoring_minutes')}min, bull={data.get('bull_percent')}%")
        
        return jsonify({
            "status": "success",
            "message": "Signal saved with full KIRA data",
            "signal_id": signal_id,
            "data": {
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "timestamp": timestamp.isoformat(),
                "has_kiria_data": has_kiria_data,
                "kiria_fields": {
                    "monitoring_minutes": data.get('monitoring_minutes'),
                    "delta_15min": data.get('delta_15min'),
                    "bull_percent": data.get('bull_percent'),
                    "dominance": data.get('dominance')
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# НОВЫЙ ЭНДПОИНТ: Получение расширенных данных KIRA
@app.route('/kiria/signals')
def get_kiria_signals():
    """Возвращает все сигналы с расширенными данными KIRA"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        # Параметры запроса
        limit = request.args.get('limit', default=50, type=int)
        offset = request.args.get('offset', default=0, type=int)
        
        cur = conn.cursor()
        
        # Проверяем, есть ли таблица kiria_full_signals
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kiria_full_signals')")
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            cur.close()
            conn.close()
            return jsonify({
                "status": "success",
                "message": "KIRA table not initialized yet",
                "count": 0,
                "signals": []
            })
        
        # Получаем данные с объединением таблиц
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
            ORDER BY ts.timestamp DESC
            LIMIT %s OFFSET %s
        ''', (limit, offset))
        
        signals = []
        for row in cur.fetchall():
            signal_id = row[0]
            full_data = row[5]  # JSON данные
            
            # Парсим JSON если он есть
            kiria_data = {}
            if full_data:
                try:
                    kiria_data = json.loads(full_data) if isinstance(full_data, str) else full_data
                except:
                    kiria_data = {}
            
            # Формируем ответ
            signal_info = {
                "id": signal_id,
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat() if row[4] else None,
                "has_kiria_data": bool(full_data),
                "monitoring_minutes": kiria_data.get("monitoring_minutes", 0),
                "delta_15min": float(kiria_data.get("delta_15min", 0)),
                "total_delta_90min": float(kiria_data.get("total_delta_90min", 0)),
                "bull_percent": float(kiria_data.get("bull_percent", 50)),
                "dominance": kiria_data.get("dominance", ""),
                "channel_data": kiria_data.get("channel_data", {})
            }
            
            # Добавляем все остальные поля из KIRA данных
            for key, value in kiria_data.items():
                if key not in signal_info:
                    signal_info[key] = value
            
            signals.append(signal_info)
        
        # Получаем общее количество
        cur.execute("SELECT COUNT(*) FROM trading_signals")
        total_count = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "signals": signals
        })
        
    except Exception as e:
        logger.error(f"Error in get_kiria_signals: {e}")
        return jsonify({"error": str(e)}), 500

# НОВЫЙ ЭНДПОИНТ: Получение одного сигнала KIRA по ID
@app.route('/kiria/signal/<int:signal_id>')
def get_kiria_signal(signal_id):
    """Возвращает один сигнал с расширенными данными KIRA по ID"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
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
            WHERE ts.id = %s
        ''', (signal_id,))
        
        row = cur.fetchone()
        
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "Signal not found"}), 404
        
        # Парсим JSON данные
        full_data = row[5]
        kiria_data = {}
        if full_data:
            try:
                kiria_data = json.loads(full_data) if isinstance(full_data, str) else full_data
            except:
                kiria_data = {}
        
        # Формируем ответ
        signal_info = {
            "id": row[0],
            "symbol": row[1],
            "signal": row[2],
            "price": float(row[3]),
            "timestamp": row[4].isoformat() if row[4] else None,
            "has_kiria_data": bool(full_data),
            "monitoring_minutes": kiria_data.get("monitoring_minutes", 0),
            "delta_15min": float(kiria_data.get("delta_15min", 0)),
            "total_delta_90min": float(kiria_data.get("total_delta_90min", 0)),
            "bull_percent": float(kiria_data.get("bull_percent", 50)),
            "dominance": kiria_data.get("dominance", ""),
            "channel_data": kiria_data.get("channel_data", {}),
            "full_kiria_data": kiria_data  # Все данные целиком
        }
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "signal": signal_info
        })
        
    except Exception as e:
        logger.error(f"Error in get_kiria_signal: {e}")
        return jsonify({"error": str(e)}), 500

# Существующие эндпоинты (оставляем без изменений)
@app.route('/signals')
def get_signals():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        limit = request.args.get('limit', default=50, type=int)
        
        cur = conn.cursor()
        cur.execute('''
            SELECT id, symbol, signal, price, timestamp, processed 
            FROM trading_signals 
            ORDER BY timestamp DESC 
            LIMIT %s
        ''', (limit,))
        
        signals = []
        for row in cur.fetchall():
            signals.append({
                "id": row[0],
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat(),
                "processed": row[5]
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "signals": signals
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/signals/active')
def get_active_signals():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        cur.execute('''
            SELECT id, symbol, signal, price, timestamp 
            FROM trading_signals 
            WHERE processed = FALSE 
            ORDER BY timestamp DESC
        ''')
        
        signals = []
        for row in cur.fetchall():
            signals.append({
                "id": row[0],
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat()
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "signals": signals
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Server starting on port {port}")
    logger.info(f"✅ KIRA endpoints available:")
    logger.info(f"   - GET /kiria/signals")
    logger.info(f"   - GET /kiria/signal/<id>")
    app.run(host='0.0.0.0', port=port)
