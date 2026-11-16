/**
 * Resilient WebSocket Client with reconnect backoff & event dispatch
 */

class WebSocketClient {
  constructor(path, onMessageCallback) {
    this.path = path;
    this.onMessageCallback = onMessageCallback;
    this.socket = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.reconnectDelay = 2000;
    this.isConnected = false;
    this.connect();
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${this.path}`;

    try {
      this.socket = new WebSocket(wsUrl);

      this.socket.onopen = () => {
        console.log(`WebSocket connected to ${this.path}`);
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.updateNetworkIndicator(true);
      };

      this.socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (this.onMessageCallback) {
            this.onMessageCallback(payload);
          }
        } catch (e) {
          console.error("Error parsing WebSocket message:", e);
        }
      };

      this.socket.onclose = () => {
        this.isConnected = false;
        this.updateNetworkIndicator(false);
        this.attemptReconnect();
      };

      this.socket.onerror = (err) => {
        console.warn(`WebSocket error on ${this.path}:`, err);
        this.socket.close();
      };
    } catch (e) {
      console.error("WebSocket connection failure:", e);
      this.attemptReconnect();
    }
  }

  attemptReconnect() {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      const jitter = Math.random() * 1000;
      const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts) + jitter, 15000);
      console.log(`Reconnecting WebSocket in ${(delay/1000).toFixed(1)}s (attempt ${this.reconnectAttempts})...`);
      setTimeout(() => this.connect(), delay);
    }
  }

  updateNetworkIndicator(online) {
    document.querySelectorAll('[data-ws-status]').forEach((el) => {
      el.classList.toggle('tone-success', online);
      el.classList.toggle('tone-warning', !online);
      const label = el.querySelector('[data-ws-label]');
      if (label && typeof t === 'function') label.textContent = online ? t('live') : t('polling');
    });
  }
}
