package com.evovision.hmi;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private static final String PREFS = "evovision_prefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final long RETRY_MS = 5000;

    private WebView webView;
    private ProgressBar progressBar;
    private TextView offlineView;
    private SharedPreferences prefs;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable retryRunnable = new Runnable() {
        @Override public void run() {
            if (webView != null) webView.reload();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);

        // Barra de status escura combinando com o tema do HMI
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            Window window = getWindow();
            window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
            window.setStatusBarColor(Color.parseColor("#07090d"));
            window.setNavigationBarColor(Color.parseColor("#07090d"));
        }

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#07090d"));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#07090d"));

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " EVOVisionApp/1.0");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (progressBar != null) progressBar.setVisibility(View.VISIBLE);
                handler.removeCallbacks(retryRunnable);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (progressBar != null) progressBar.setVisibility(View.GONE);
                if (offlineView != null) offlineView.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                // Servidor inacessível: mostra tela de offline e tenta de novo sozinho
                if (offlineView != null) offlineView.setVisibility(View.VISIBLE);
                if (progressBar != null) progressBar.setVisibility(View.GONE);
                handler.removeCallbacks(retryRunnable);
                handler.postDelayed(retryRunnable, RETRY_MS);
            }
        });
        webView.setWebChromeClient(new WebChromeClient());

        // Segura pressionado 2s numa área qualquer abre a config do servidor
        webView.setOnLongClickListener(v -> {
            showServerDialog();
            return true;
        });

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        // Sem material: indeterminate horizontal com 3dp — some sozinho no onPageFinished
        progressBar.setIndeterminate(true);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        progressParams.gravity = Gravity.TOP;
        progressBar.setVisibility(View.GONE);

        offlineView = new TextView(this);
        offlineView.setText("Servidor indisponível\n\nMantendo tentativa automática…\n\nToque e segure para configurar o endereço do servidor.");
        offlineView.setTextColor(Color.parseColor("#e8edf5"));
        offlineView.setTextSize(16);
        offlineView.setGravity(Gravity.CENTER);
        offlineView.setBackgroundColor(Color.parseColor("#07090dee"));
        offlineView.setVisibility(View.GONE);

        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(offlineView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(progressBar, progressParams);

        setContentView(root);
        loadServer();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private String serverUrl() {
        return prefs.getString(KEY_SERVER_URL, getString(R.string.default_server_url));
    }

    private void loadServer() {
        webView.loadUrl(serverUrl());
    }

    private void showServerDialog() {
        final EditText input = new EditText(this);
        input.setText(serverUrl());
        input.setSelection(input.getText().length());
        input.setHint("https://evolution-beta-six.vercel.app/");

        new AlertDialog.Builder(this)
                .setTitle("Servidor EVO·VISION")
                .setMessage("Endereço do servidor na sua rede (mesma Wi-Fi do PC):")
                .setView(input)
                .setPositiveButton("Salvar e recarregar", (dialog, which) -> {
                    String url = input.getText().toString().trim();
                    if (!url.startsWith("http://") && !url.startsWith("https://")) {
                        url = "http://" + url;
                    }
                    prefs.edit().putString(KEY_SERVER_URL, url).apply();
                    Toast.makeText(this, "Servidor atualizado", Toast.LENGTH_SHORT).show();
                    loadServer();
                })
                .setNegativeButton("Cancelar", null)
                .setNeutralButton("Restaurar padrão", (dialog, which) -> {
                    prefs.edit().remove(KEY_SERVER_URL).apply();
                    loadServer();
                })
                .show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(retryRunnable);
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
