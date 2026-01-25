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
    """Подключение к базе данных"""
    database_url = os.environ.get('DATABASE_URL')
    
    if not database_url:
        logger.error("❌ DATABASE_URL не установлен!")
        return None
    
    try:
        conn = psycopg2.connect(database_url, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return None

def init_database():
    """Инициализация таблиц в базе данных"""
    conn = get_db_connection()
    if not conn:
        logger.error("❌ Не могу инициализировать БД: нет подключения")
        return False
    
    try:
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trading_signals (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                signal VARCHAR(10) NOT NULL,
                price DECIMAL(15, 5) NOT NULL,
                strength DECIMAL(3, 1),
                timeframe VARCHAR(10),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE,
                raw_data JSONB
            )
        ''')
        
        cur.execute('CREATE INDEX IF NOT EXISTS idx_symbol ON trading_signals(symbol)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON trading_signals(timestamp)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_processed ON trading_signals(processed)')
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info("✅ Таблицы базы данных инициализированы")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return False

# Инициализируем базу при старте
init_database()

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "version": "2.0",
        "status": "running",
        "database": "connected" if get_db_connection() else "disconnected",
        "endpoints": {
            "webhook": "POST /webhook - Прием алертов из TradingView",
            "signals": "GET /signals - Все сигналы",
            "active_signals": "GET /signals/active - Непрочитанные сигналы",
            "health": "GET /health - Проверка здоровья"
        }
    })

@app.route('/health', methods=['GET'])
def health():
    db_status = "connected" if get_db_connection() else "disconnected"
    return jsonify({
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "service": "TradingView Proxy",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
        "note": "Signals are saved to PostgreSQL database" if db_status == "connected" else "⚠️ Database not connected - signals will be lost!"
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной эндпоинт для приема вебхуков из TradingView"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Проверяем обязательные поля
        required = ['symbol', 'signal', 'price']
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        symbol = data['symbol'].upper()
        signal = data['signal'].upper()
        price = float(data['price'])
        strength = float(data.get('strength', 8.5))
        timeframe = data.get('timeframe', '5m')
        
        logger.info(f"✅ Данные верифицированы: {symbol} {signal} @ {price}")
        
        # Подключаемся к базе данных
        conn = get_db_connection()
        if not conn:
            logger.error("❌ Не могу подключиться к базе данных")
            return jsonify({
                "status": "warning",
                "message": "Signal received but database not connected"
            }), 200
        
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO trading_signals 
                (symbol, signal, price, strength, timeframe, raw_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, timestamp
            ''', (symbol, signal, price, strength, timeframe, json.dumps(data)))
            
            signal_id, timestamp = cur.fetchone()
            conn.commit()
            
            logger.info(f"💾 Сигнал сохранен в БД с ID: {signal_id}")
            
            cur.close()
            conn.close()
            
            return jsonify({
                "status": "success",
                "message": "Signal saved to database",
                "signal_id": signal_id,
                "data": {
                    "id": signal_id,
                    "symbol": symbol,
                    "signal": signal,
                    "price": price,
                    "strength": strength,
                    "timeframe": timeframe,
                    "timestamp": timestamp.isoformat() if timestamp else datetime.now().isoformat()
                }
            }), 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении в БД: {e}")
            conn.rollback()
            return jsonify({"error": f"Database error: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/signals', methods=['GET'])
def get_signals():
    """Получить все сохраненные сигналы"""
    try:
        limit = request.args.get('limit', 50, type=int)
        symbol = request.args.get('symbol')
        
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database not connected",
                "signals": []
            }), 200
        
        cur = conn.cursor()
        
        if symbol:
            cur.execute('''
                SELECT id, symbol, signal, price, strength, timeframe, timestamp, processed, raw_data
                FROM trading_signals
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (symbol.upper(), limit))
        else:
            cur.execute('''
                SELECT id, symbol, signal, price, strength, timeframe, timestamp, processed, raw_data
                FROM trading_signals
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (limit,))
        
        signals = cur.fetchall()
        cur.close()
        conn.close()
        
        result = []
        for sig in signals:
            result.append({
                "id": sig[0],
                "symbol": sig[1],
                "signal": sig[2],
                "price": float(sig[3]),
                "strength": float(sig[4]) if sig[4] else None,
                "timeframe": sig[5],
                "timestamp": sig[6].isoformat() if sig[6] else None,
                "processed": sig[7],
                "raw_data": sig[8]
            })
        
        return jsonify({
            "status": "success",
            "count": len(result),
            "signals": result
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения сигналов: {e}")
        return jsonify({"error": str(e), "signals": []}), 500

@app.route('/signals/active', methods=['GET'])
def get_active_signals():
    """Получить только непрочитанные сигналы"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database not connected",
                "signals": []
            }), 200
        
        cur = conn.cursor()
        cur.execute('''
            SELECT id, symbol, signal, price, strength, timeframe, timestamp, raw_data
            FROM trading_signals
            WHERE processed = FALSE
            ORDER BY timestamp DESC
        ''')
        
        signals = cur.fetchall()
        cur.close()
        conn.close()
        
        result = []
        for sig in signals:
            result.append({
                "id": sig[0],
                "symbol": sig[1],
                "signal": sig[2],
                "price": float(sig[3]),
                "strength": float(sig[4]) if sig[4] else None,
                "timeframe": sig[5],
                "timestamp": sig[6].isoformat() if sig[6] else None,
                "raw_data": sig[7]
            })
        
        return jsonify({
            "status": "success",
            "count": len(result),
            "signals": result
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения активных сигналов: {e}")
        return jsonify({"error": str(e), "signals": []}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Сервис запущен на порту {port}")
    logger.info(f"📡 Webhook URL: https://tradingview-proxy-h71n.onrender.com/webhook")
    logger.info(f"💾 Database: {'connected' if get_db_connection() else 'disconnected'}")
    app.run(host='0.0.0.0', port=port, debug=False)
