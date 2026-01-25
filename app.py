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
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database tables initialized")
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
        "webhook_url": "https://tradingview-proxy-h71n.onrender.com/webhook",
        "endpoints": ["/health", "/webhook", "/signals", "/signals/active"]
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "database": "connected" if get_db_connection() else "disconnected",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "No JSON data"}), 400
        
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
        cur.execute('''
            INSERT INTO trading_signals (symbol, signal, price)
            VALUES (%s, %s, %s)
            RETURNING id, timestamp
        ''', (symbol, signal, price))
        
        signal_id, timestamp = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Signal saved",
            "signal_id": signal_id,
            "data": {
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "timestamp": timestamp.isoformat()
            }
        })
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/signals')
def get_signals():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        cur.execute('SELECT id, symbol, signal, price, timestamp, processed FROM trading_signals ORDER BY timestamp DESC LIMIT 50')
        
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
        cur.execute('SELECT id, symbol, signal, price, timestamp FROM trading_signals WHERE processed = FALSE ORDER BY timestamp DESC')
        
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
    logger.info(f"Server starting on port {port}")
    app.run(host='0.0.0.0', port=port)
