package com.killercatfish.redzone;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** The board, full screen. The TV draws the page itself, so it's sharp and on time (casting a tab isn't). */
public class MainActivity extends Activity {
    private static final long RETRY_MS = 10_000;
    private static final String OFFLINE = "<body style='background:#06090f;color:#8899b0;font:32px sans-serif;"
            + "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            + "Can't reach the board. Trying again…</body>";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView web;
    private String url;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        url = getString(R.string.board_url);
        String test = getIntent().getStringExtra("url");   // for testing: adb shell am start ... --es url http://...
        if (test != null) url = test;

        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // the horn and the voice play without a click
        // The board is a 1920-wide stage. At the TV's 2x density the page would get 960 px, and the web view
        // widens the page to fit the stage and shows only its top-left quarter. One page pixel per screen pixel.
        web.setInitialScale(100);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (!req.isForMainFrame()) return;      // a missing logo isn't worth a retry
                v.loadData(OFFLINE, "text/html; charset=utf-8", null);
                handler.removeCallbacksAndMessages(null);
                handler.postDelayed(() -> web.loadUrl(url), RETRY_MS);
            }
        });
        setContentView(web);
        web.requestFocus();                             // remote arrows reach the page's key handler
        web.loadUrl(url);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        web.resumeTimers();
    }

    // Leaving the app (Home button) silences it rather than talking over whatever's on next.
    @Override
    protected void onPause() {
        web.onPause();
        web.pauseTimers();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        web.destroy();
        super.onDestroy();
    }
}
