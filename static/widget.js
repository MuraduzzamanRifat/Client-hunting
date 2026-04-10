/**
 * AI Chat Widget — Embed Snippet
 *
 * Usage: Add this to any website:
 * <script src="https://your-app.koyeb.app/static/widget.js" data-store="demo"></script>
 */
(function() {
    var script = document.currentScript;
    var storeId = (script && script.getAttribute('data-store')) || 'demo';
    var baseUrl = (script && script.src) ? script.src.replace('/static/widget.js', '') : '';

    // Create iframe
    var iframe = document.createElement('iframe');
    iframe.src = baseUrl + '/widget?store=' + storeId;
    iframe.id = 'ai-chat-widget-frame';
    iframe.style.cssText = 'position:fixed;bottom:90px;right:20px;width:380px;height:520px;border:none;z-index:2147483646;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.15);display:none;';

    // Create toggle button
    var btn = document.createElement('div');
    btn.id = 'ai-chat-widget-btn';
    btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    btn.style.cssText = 'position:fixed;bottom:20px;right:20px;width:60px;height:60px;background:#2D7D46;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:2147483647;box-shadow:0 4px 16px rgba(0,0,0,0.2);transition:transform 0.2s;';

    btn.onmouseenter = function() { btn.style.transform = 'scale(1.1)'; };
    btn.onmouseleave = function() { btn.style.transform = 'scale(1)'; };

    var isOpen = false;
    btn.onclick = function() {
        isOpen = !isOpen;
        iframe.style.display = isOpen ? 'block' : 'none';
        btn.innerHTML = isOpen
            ? '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
            : '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    };

    // Listen for close from iframe
    window.addEventListener('message', function(e) {
        if (e.data === 'close-widget') {
            isOpen = false;
            iframe.style.display = 'none';
            btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
        }
    });

    document.body.appendChild(iframe);
    document.body.appendChild(btn);
})();
