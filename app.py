import os
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Хранилище сигналов в памяти
signals_store = []

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной вебхук для TradingView"""
    try:
        data = request.json
        
        # Проверяем обязательные поля
        if not data or 'symbol' not in data or 'signal' not in data or 'price' not in data:
            return jsonify({"error": "Missing required fields"}), 400
        
        # Логируем
        print(f"📨 Signal received: {data['symbol']} {data['signal']} @ {data['price']}")
        
        # Сохраняем сигнал
        signal = {
            'id': len(signals_store) + 1,
            'symbol': data['symbol'],
            'signal': data['signal'],
            'price': data['price'],
            'timestamp': datetime.now().isoformat(),
            'data': data
        }
        
        signals_store.append(signal)
        
        # Ограничиваем до 50 сигналов
        if len(signals_store) > 50:
            signals_store.pop(0)
        
        return jsonify({
            "status": "success",
            "message": "Signal saved in memory",
            "signal_id": signal['id'],
            "timestamp": signal['timestamp']
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/signals', methods=['GET'])
def get_signals():
    """Получить все сигналы"""
    return jsonify({
        "status": "success",
        "count": len(signals_store),
        "signals": signals_store
    }), 200

@app.route('/clear', methods=['POST'])
def clear_signals():
    """Очистить все сигналы"""
    signals_store.clear()
    return jsonify({"status": "success", "message": "All signals cleared"}), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "service": "TradingView Proxy",
        "memory_signals": len(signals_store),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "version": "1.0",
        "endpoints": {
            "webhook": "POST /webhook - Accept TradingView alerts",
            "signals": "GET /signals - Get all signals in memory",
            "health": "GET /health - Health check",
            "clear": "POST /clear - Clear all signals"
        },
        "usage": "Webhook URL: https://tradingview-proxy-h71n.onrender.com/webhook",
        "note": "Signals stored in memory. They will be lost when service restarts."
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
