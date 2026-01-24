import os
import requests
import json
from datetime import datetime
from flask import Flask, request, jsonify
import psycopg2
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Конфигурация
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', 'default-secret-change-me')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Подключение к БД
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Создание таблицы для сигналов
def init_db():
    try:
        conn = get_db_connection()
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
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ Database error: {e}")

# Инициализация БД при старте
init_db()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной вебхук для TradingView"""
    try:
        data = request.json
        
        # Валидация
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        # Проверяем обязательные поля
        required = ['symbol', 'signal', 'price']
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400
        
        # Логируем
        print(f"📨 Signal received: {data['symbol']} {data['signal']} @ {data['price']}")
        
        # Сохраняем в БД
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute('''
            INSERT INTO trading_signals 
            (symbol, signal, price, strength, timeframe, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (
            data['symbol'],
            data['signal'],
            data['price'],
            data.get('strength'),
            data.get('timeframe', '5m'),
            json.dumps(data)
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Signal saved",
            "symbol": data['symbol'],
            "signal": data['signal'],
            "price": data['price'],
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/signals', methods=['GET'])
def get_signals():
    """Получить все непрочитанные сигналы"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute('''
            SELECT * FROM trading_signals 
            WHERE processed = FALSE 
            ORDER BY timestamp DESC
        ''')
        
        signals = cur.fetchall()
        cur.close()
        conn.close()
        
        # Форматируем результат
        result = []
        for signal in signals:
            result.append({
                "id": signal[0],
                "symbol": signal[1],
                "signal": signal[2],
                "price": float(signal[3]),
                "strength": float(signal[4]) if signal[4] else None,
                "timeframe": signal[5],
                "timestamp": signal[6].isoformat(),
                "raw_data": signal[8]
            })
        
        return jsonify({
            "status": "success",
            "count": len(result),
            "signals": result
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "service": "TradingView Proxy",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "version": "1.0",
        "endpoints": {
            "webhook": "POST /webhook - Accept TradingView alerts",
            "signals": "GET /signals - Get all pending signals",
            "health": "GET /health - Health check"
        },
        "usage": "Use in TradingView: https://your-url.onrender.com/webhook"
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)