package cmcoder.idea;

import java.awt.Color;
import java.awt.Font;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;

import javax.swing.JComponent;
import javax.swing.SwingUtilities;
import javax.swing.UIManager;

import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.callback.CefContextMenuParams;
import org.cef.callback.CefMenuModel;
import org.cef.handler.CefContextMenuHandlerAdapter;
import org.cef.handler.CefLifeSpanHandlerAdapter;
import org.cef.handler.CefLoadHandlerAdapter;
import org.cef.handler.CefRequestHandlerAdapter;
import org.cef.network.CefRequest;

import com.intellij.ide.ui.LafManagerListener;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.application.PathManager;
import com.intellij.openapi.util.Disposer;
import com.intellij.ui.jcef.JBCefBrowser;
import com.intellij.ui.jcef.JBCefBrowserBase;
import com.intellij.ui.jcef.JBCefClient;
import com.intellij.ui.jcef.JBCefJSQuery;

import cmcoder.ide.core.Json;

/**
 * One of the shared pages (chat or navigator) in JCEF, IntelliJ's embedded
 * Chromium: the page the web-panel README describes, the plugin's own files
 * only, messages both ways (H5, H18, H19, H20).
 *
 * <p>Messages from the page come through a {@link JBCefJSQuery} behind
 * {@code window.cmcoderHostPost}, defined once the plugin's own page has loaded,
 * with a secret made for that load: only messages carrying it are taken
 * (another page can't know it). Navigation away from the page and new windows
 * are refused; if another page ever loads anyway, the page is loaded again.
 */
public final class Panel implements Disposable {
    private final String pageUrl;
    private final Consumer<String> onMessage;
    private final JBCefBrowser browser;
    private final JBCefJSQuery query;
    private volatile boolean loaded;
    private volatile String secret = "";
    private volatile boolean disposed;
    /** A prompt to put in the input box that came while the page loaded (Ask About Selection opening the chat). */
    private volatile String pendingPrefill;

    private Panel(String pageUrl, Consumer<String> onMessage) {
        this.pageUrl = pageUrl;
        this.onMessage = onMessage;
        browser = JBCefBrowser.createBuilder().setEnableOpenDevToolsMenuItem(false).build();
        Disposer.register(this, browser);
        query = JBCefJSQuery.create((JBCefBrowserBase) browser);
        Disposer.register(this, query);
        query.addHandler(text -> {
            String prefix = secret + ":";
            if (secret.isEmpty() || text == null || !text.startsWith(prefix)) return null;
            try {
                onMessage.accept(text.substring(prefix.length()));
            } catch (RuntimeException e) {
                Plugin.error("A message from the panel failed", e);
            }
            return null;
        });
        JBCefClient client = browser.getJBCefClient();
        CefBrowser cef = browser.getCefBrowser();
        client.addLoadHandler(new CefLoadHandlerAdapter() {
            @Override
            public void onLoadEnd(CefBrowser b, CefFrame frame, int httpStatusCode) {
                if (disposed || frame == null || !frame.isMain()) return;
                if (samePage(b.getURL())) {
                    secret = nonce();
                    b.executeJavaScript("window.cmcoderHostPost = function (json) { "
                            + query.inject(Json.write(secret + ":") + " + String(json)") + " };", pageUrl, 0);
                    loaded = true;
                    applyTheme();
                    String prefill = pendingPrefill;
                    pendingPrefill = null;
                    if (prefill != null) post(prefill);
                } else {
                    loaded = false;
                    secret = "";
                    b.loadURL(pageUrl);
                }
            }
        }, cef);
        client.addRequestHandler(new CefRequestHandlerAdapter() {
            @Override
            public boolean onBeforeBrowse(CefBrowser b, CefFrame frame, CefRequest request, boolean userGesture, boolean isRedirect) {
                // Never leave the page; links go through openLink (H18, H19). True cancels.
                return frame != null && frame.isMain() && !samePage(request.getURL());
            }
        }, cef);
        client.addLifeSpanHandler(new CefLifeSpanHandlerAdapter() {
            @Override
            public boolean onBeforePopup(CefBrowser b, CefFrame frame, String targetUrl, String targetFrameName) {
                return true; // no new windows
            }
        }, cef);
        client.addContextMenuHandler(new CefContextMenuHandlerAdapter() {
            @Override
            public void onBeforeContextMenu(CefBrowser b, CefFrame frame, CefContextMenuParams params, CefMenuModel model) {
                model.clear(); // no "reload" or developer tools
            }
        }, cef);
        ApplicationManager.getApplication().getMessageBus().connect(this)
                .subscribe(LafManagerListener.TOPIC, (LafManagerListener) source -> applyTheme());
    }

    /**
     * The page ("chat" or "navigator"), disposed with {@code parent}; its messages
     * go to {@code onMessage} (on a JCEF thread). Throws IOException when the
     * plugin's panel files are missing.
     */
    public static Panel create(String page, Consumer<String> onMessage, Disposable parent) throws IOException {
        Panel panel = new Panel(writePage(page).toUri().toString(), onMessage);
        Disposer.register(parent, panel);
        panel.browser.loadURL(panel.pageUrl);
        return panel;
    }

    public JComponent component() {
        return browser.getComponent();
    }

    private boolean samePage(String url) {
        return url != null && stripFragment(url).equalsIgnoreCase(stripFragment(pageUrl));
    }

    private static String stripFragment(String url) {
        int hash = url.indexOf('#');
        return hash < 0 ? url : url.substring(0, hash);
    }

    /** A message (JSON) to the page; any thread. Dropped while the page loads: it asks again with "ready". */
    public void post(String json) {
        if (disposed) return;
        if (!loaded) {
            if (json.startsWith("{\"kind\":\"prefill\"")) pendingPrefill = json;
            return;
        }
        // The JSON travels as a string literal and is parsed in the page.
        browser.getCefBrowser().executeJavaScript("window.postMessage(JSON.parse(" + Json.write(json) + "),'*')", pageUrl, 0);
    }

    public boolean isLoaded() {
        return loaded;
    }

    @Override
    public void dispose() {
        disposed = true;
        loaded = false;
        secret = "";
    }

    // -- the page ----------------------------------------------------------------------

    /** Writes {@code <page>.html} for this start of the IDE, in its system folder. */
    static Path writePage(String page) throws IOException {
        Path panel = Plugin.file("panel");
        if (panel == null || !Files.isRegularFile(panel.resolve(page + ".js"))) {
            throw new IOException("the plugin's panel files are missing (install the plugin again)");
        }
        String source = panel.toUri().toString().replaceAll("/$", "");
        String nonce = nonce();
        String icon = Files.isRegularFile(panel.resolve("icon.png")) ? " data-icon=\"" + attr(source + "/icon.png") + "\"" : "";
        String html = "<!DOCTYPE html>\n<html lang=\"en\"><head>\n"
                + "  <meta charset=\"UTF-8\">\n"
                + "  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src file:; img-src file: data:; script-src 'nonce-"
                + nonce + "'\">\n"
                + "  <link href=\"" + attr(source + "/" + page + ".css") + "\" rel=\"stylesheet\">\n"
                + "</head>\n<body data-product=\"" + attr(Brand.product()) + "\"" + icon + " data-host=\"function\">\n"
                + "  <div id=\"app\"></div>\n"
                + "  <script nonce=\"" + nonce + "\" src=\"" + attr(source + "/" + page + ".js") + "\"></script>\n"
                + "</body></html>\n";
        Path folder = Path.of(PathManager.getSystemPath(), "cmcoder", "pages");
        Files.createDirectories(folder);
        Path file = folder.resolve(page + ".html");
        Files.writeString(file, html, StandardCharsets.UTF_8);
        return file;
    }

    private static String nonce() {
        byte[] bytes = new byte[18];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    static String attr(String s) {
        return s.replace("&", "&amp;").replace("\"", "&quot;").replace("<", "&lt;").replace(">", "&gt;");
    }

    // -- the theme (H20) -------------------------------------------------------------------

    /** Sets the page's CSS variables from the IDE's theme (light or dark), now and when it changes. */
    void applyTheme() {
        if (!SwingUtilities.isEventDispatchThread()) {
            SwingUtilities.invokeLater(this::applyTheme);
            return;
        }
        if (disposed || !loaded) return;
        StringBuilder js = new StringBuilder("(function(){var s=document.documentElement.style;");
        for (Map.Entry<String, String> v : themeVariables().entrySet()) {
            js.append("s.setProperty(").append(Json.write(v.getKey())).append(',').append(Json.write(v.getValue())).append(");");
        }
        js.append("})()");
        browser.getCefBrowser().executeJavaScript(js.toString(), pageUrl, 0);
    }

    /** theme.json's defaults for a light or dark IDE, with its own background, text and font (EDT). */
    static Map<String, String> themeVariables() {
        Color background = color("Panel.background", Color.WHITE);
        Color foreground = color("Label.foreground", Color.BLACK);
        boolean dark = luminance(background) < 0.5;
        Map<String, String> vars = new LinkedHashMap<>();
        Path themeFile = Plugin.file("panel/theme.json");
        if (themeFile != null) {
            try {
                Map<String, Object> all = Json.object(Json.parseObject(Files.readString(themeFile, StandardCharsets.UTF_8)), "variables");
                if (all != null) {
                    for (Map.Entry<String, Object> e : all.entrySet()) {
                        @SuppressWarnings("unchecked")
                        Map<String, Object> v = (Map<String, Object>) e.getValue();
                        String value = Json.string(v, dark ? "dark" : "light");
                        if (value != null) vars.put(e.getKey(), value);
                    }
                }
            } catch (IOException | RuntimeException e) {
                Plugin.error("Could not read the panel's theme.json", e);
            }
        }
        String bg = hex(background);
        String fg = hex(foreground);
        vars.put("--vscode-sideBar-background", bg);
        vars.put("--vscode-foreground", fg);
        vars.put("--vscode-input-foreground", fg);
        vars.put("--vscode-button-secondaryForeground", fg);
        Font ui = UIManager.getFont("Label.font");
        if (ui != null) {
            vars.put("--vscode-font-family", "\"" + ui.getFamily().replace("\"", "") + "\", sans-serif");
            vars.put("--vscode-font-size", ui.getSize() + "px");
        }
        vars.put("--vscode-editor-font-family", "monospace");
        return vars;
    }

    private static Color color(String key, Color fallback) {
        Color c = UIManager.getColor(key);
        return c == null ? fallback : c;
    }

    static double luminance(Color c) {
        return (0.2126 * c.getRed() + 0.7152 * c.getGreen() + 0.0722 * c.getBlue()) / 255.0;
    }

    static String hex(Color c) {
        return String.format(Locale.ROOT, "#%02x%02x%02x", c.getRed(), c.getGreen(), c.getBlue());
    }
}
