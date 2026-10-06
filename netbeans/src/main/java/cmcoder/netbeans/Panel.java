package cmcoder.netbeans;

import java.awt.BorderLayout;
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
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

import javax.swing.JPanel;
import javax.swing.JTextArea;
import javax.swing.SwingUtilities;
import javax.swing.UIManager;

import javafx.application.Platform;
import javafx.concurrent.Worker;
import javafx.embed.swing.JFXPanel;
import javafx.scene.Scene;
import javafx.scene.web.WebEngine;
import javafx.scene.web.WebView;

import org.openide.modules.Places;

import cmcoder.ide.core.Json;

/**
 * One of the shared pages (chat or navigator) in a JavaFX WebView (NetBeans has
 * no browser of its own): the page the web-panel README describes, the plugin's
 * own files only, messages both ways (H5, H18, H19, H20).
 *
 * <p>Messages from the page come through {@code alert()}: once its own page
 * has loaded, the plugin defines {@code window.cmcoderHostPost} as an alert
 * with a marker and a secret made for that load, and only alerts carrying both
 * are messages (another page can't know the secret). No Java object is exposed
 * to the page's script (JavaFX's JSObject members would let a script reach any
 * of their public methods).
 *
 * <p>JavaFX can't refuse a navigation: one away from the page is cancelled as it
 * starts, and if another page ever loads anyway, the chat page is loaded again.
 */
public final class Panel extends JPanel {
    private static final long serialVersionUID = 1L;
    /** Tests only: a test-driver.js to load after the page's script (never set in a release). */
    public static final String TEST_DRIVER = "cmcoder.testDriver";
    private static final String MARKER = "\u0001cmcoder:";

    private final transient Consumer<String> onMessage;
    private final String pageUrl;
    private final JFXPanel fx;
    private transient WebEngine engine; // JavaFX thread
    private volatile boolean loaded;
    private volatile String secret = "";
    private volatile boolean closed;

    private Panel(String pageUrl, Consumer<String> onMessage) {
        super(new BorderLayout());
        this.pageUrl = pageUrl;
        this.onMessage = onMessage;
        Platform.setImplicitExit(false); // closing the last panel must not end JavaFX for good
        fx = new JFXPanel();
        add(fx, BorderLayout.CENTER);
    }

    /**
     * The page ("chat" or "navigator"); its messages go to {@code onMessage}
     * (on the JavaFX thread). If it can't be prepared, a component that says
     * why is returned instead, as {@code error}, and null. Call {@link #load}
     * once the panel is registered.
     */
    public static Panel create(String page, Consumer<String> onMessage, Consumer<javax.swing.JComponent> error) {
        Path html;
        try {
            html = writePage(page);
        } catch (IOException | RuntimeException e) {
            Plugin.error("Could not write the " + page + " page", e);
            error.accept(message(Brand.product() + " could not prepare its panel: " + e.getMessage()));
            return null;
        }
        try {
            return new Panel(html.toUri().toString(), onMessage);
        } catch (RuntimeException | LinkageError e) {
            Plugin.error("No JavaFX for the " + Brand.product() + " panel", e);
            error.accept(message(Brand.product() + " could not start its panel (JavaFX): " + e
                    + "\n\nOn Linux, JavaFX needs GTK 3 (the libgtk-3-0 package). Copy Diagnostics has the details."));
            return null;
        }
    }

    private static javax.swing.JComponent message(String text) {
        JTextArea area = new JTextArea(text);
        area.setEditable(false);
        area.setLineWrap(true);
        area.setWrapStyleWord(true);
        return area;
    }

    /** Loads the page (separate from create: the caller registers the panel first). */
    public void load() {
        Platform.runLater(() -> {
            if (closed) return;
            WebView view = new WebView();
            view.setContextMenuEnabled(false); // no "reload"/"inspect" menu
            engine = view.getEngine();
            engine.setJavaScriptEnabled(true);
            engine.setCreatePopupHandler(features -> null); // no new windows (H18)
            engine.setOnAlert(e -> {
                String data = e.getData();
                String prefix = MARKER + secret + ":";
                if (secret.isEmpty() || data == null || !data.startsWith(prefix)) return;
                try {
                    onMessage.accept(data.substring(prefix.length()));
                } catch (RuntimeException ex) {
                    Plugin.error("A message from the panel failed", ex);
                }
            });
            engine.getLoadWorker().stateProperty().addListener((obs, old, state) -> {
                if (closed) return; // closing empties the browser: nothing to guard or reload
                boolean ours = samePage(engine.getLocation());
                if (state == Worker.State.SCHEDULED && !ours) {
                    // Never leave the page; links go through openLink (H18, H19).
                    secret = "";
                    engine.getLoadWorker().cancel();
                } else if (state == Worker.State.SUCCEEDED && ours) {
                    secret = nonce();
                    loaded = true;
                    engine.executeScript("window.cmcoderHostPost = function (json) { alert(" + Json.write(MARKER + secret + ":")
                            + " + String(json)); };");
                    applyTheme();
                } else if (state == Worker.State.SUCCEEDED || state == Worker.State.CANCELLED) {
                    // Another page, or a cancelled navigation: is the document still ours?
                    Object url = engine.executeScript("document.URL");
                    if (!samePage(String.valueOf(url))) {
                        loaded = false;
                        secret = "";
                        engine.load(pageUrl);
                    } else if (!loaded || secret.isEmpty()) {
                        secret = nonce();
                        loaded = true;
                        engine.executeScript("window.cmcoderHostPost = function (json) { alert(" + Json.write(MARKER + secret + ":")
                                + " + String(json)); };");
                    }
                }
            });
            fx.setScene(new Scene(view));
            engine.load(pageUrl);
        });
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
        if (closed) return;
        Platform.runLater(() -> {
            if (closed || !loaded || engine == null) return;
            // The JSON travels as a string literal and is parsed in the page.
            engine.executeScript("window.postMessage(JSON.parse(" + Json.write(json) + "),'*')");
        });
    }

    /** Tests: a script's result as a string ("null" while the page isn't loaded). */
    public CompletableFuture<String> evaluate(String script) {
        CompletableFuture<String> result = new CompletableFuture<>();
        Platform.runLater(() -> {
            try {
                if (engine == null || !loaded) {
                    result.complete("null");
                    return;
                }
                Object value = engine.executeScript(script);
                result.complete(value == null ? "null" : String.valueOf(value));
            } catch (RuntimeException e) {
                result.complete("error: " + e.getMessage());
            }
        });
        return result;
    }

    /** Tests: the address of the document shown now. */
    public CompletableFuture<String> pageLocation() {
        return evaluate("document.URL");
    }

    public boolean isLoaded() {
        return loaded;
    }

    public void close() {
        closed = true;
        secret = "";
        Platform.runLater(() -> {
            if (engine != null) engine.load(null);
            fx.setScene(null);
        });
    }

    // -- the page ----------------------------------------------------------------------

    /** Writes {@code <page>.html} for this start of NetBeans, in the user folder's cache. */
    static Path writePage(String page) throws IOException {
        Path panel = Plugin.file("panel");
        if (panel == null || !Files.isRegularFile(panel.resolve(page + ".js"))) {
            throw new IOException("the plugin's panel files are missing (install the plugin again)");
        }
        String source = panel.toUri().toString().replaceAll("/$", "");
        String nonce = nonce();
        String testDriver = System.getProperty(TEST_DRIVER);
        String testScript = testDriver == null || testDriver.isBlank() ? ""
                : "\n  <script nonce=\"" + nonce + "\" src=\"" + attr(Path.of(testDriver).toUri().toString()) + "\"></script>";
        String icon = Files.isRegularFile(panel.resolve("icon.png")) ? " data-icon=\"" + attr(source + "/icon.png") + "\"" : "";
        String html = "<!DOCTYPE html>\n<html lang=\"en\"><head>\n"
                + "  <meta charset=\"UTF-8\">\n"
                + "  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src file:; img-src file: data:; script-src 'nonce-"
                + nonce + "'\">\n"
                + "  <link href=\"" + attr(source + "/" + page + ".css") + "\" rel=\"stylesheet\">\n"
                + "</head>\n<body data-product=\"" + attr(Brand.product()) + "\"" + icon + " data-host=\"function\">\n"
                + "  <div id=\"app\"></div>\n"
                + "  <script nonce=\"" + nonce + "\" src=\"" + attr(source + "/" + page + ".js") + "\"></script>" + testScript + "\n"
                + "</body></html>\n";
        Path folder = Places.getCacheSubdirectory("cmcoder/pages").toPath();
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

    /** Sets the page's CSS variables from NetBeans' look and feel (light or dark). */
    void applyTheme() {
        if (!SwingUtilities.isEventDispatchThread()) {
            SwingUtilities.invokeLater(this::applyTheme);
            return;
        }
        Map<String, String> vars = themeVariables();
        StringBuilder js = new StringBuilder("(function(){var s=document.documentElement.style;");
        for (Map.Entry<String, String> v : vars.entrySet()) {
            js.append("s.setProperty(").append(Json.write(v.getKey())).append(',').append(Json.write(v.getValue())).append(");");
        }
        js.append("})()");
        Platform.runLater(() -> {
            if (engine != null && loaded) engine.executeScript(js.toString());
        });
    }

    /** theme.json's defaults for a light or dark NetBeans, with its own background, text and fonts (EDT). */
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
