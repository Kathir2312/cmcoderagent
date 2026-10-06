package cmcoder.eclipse;

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

import org.eclipse.jface.resource.JFaceResources;
import org.eclipse.jface.util.IPropertyChangeListener;
import org.eclipse.swt.SWT;
import org.eclipse.swt.SWTError;
import org.eclipse.swt.browser.Browser;
import org.eclipse.swt.browser.BrowserFunction;
import org.eclipse.swt.browser.LocationAdapter;
import org.eclipse.swt.browser.LocationEvent;
import org.eclipse.swt.browser.ProgressAdapter;
import org.eclipse.swt.browser.ProgressEvent;
import org.eclipse.swt.graphics.FontData;
import org.eclipse.swt.graphics.RGB;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Label;
import org.eclipse.swt.widgets.Menu;
import org.eclipse.ui.PlatformUI;

import cmcoder.ide.core.Json;

/**
 * One of the shared pages (chat or navigator) in an SWT browser: the page the
 * web-panel README describes, the plugin's own files only, messages both ways
 * (H5, H16, H18, H19).
 */
public final class Panel {
    /** Tests only: a test-driver.js to load after the page's script (never set in a release). */
    public static final String TEST_DRIVER = "cmcoder.testDriver";

    private final Browser browser;
    private final String pageUrl;
    private final Consumer<String> onMessage;
    private final IPropertyChangeListener themeListener = e -> Display.getDefault().asyncExec(this::applyTheme);
    private boolean loaded;

    private Panel(Browser browser, String pageUrl, Consumer<String> onMessage) {
        this.browser = browser;
        this.pageUrl = pageUrl;
        this.onMessage = onMessage;
    }

    /**
     * The page ("chat" or "navigator") in {@code parent}; its messages go to
     * {@code onMessage} on the UI thread. If the browser can't start (no
     * WebView2 on Windows, no WebKitGTK on Linux), a label says what's
     * missing and null is returned. Call {@link #load} once it's registered.
     */
    public static Panel create(Composite parent, String page, Consumer<String> onMessage) {
        parent.setLayout(new FillLayout());
        Path html;
        try {
            html = writePage(page);
        } catch (IOException | RuntimeException e) {
            Activator.error("Could not write the " + page + " page", e);
            message(parent, Brand.product() + " could not prepare its panel: " + e.getMessage());
            return null;
        }
        Browser browser;
        try {
            // Edge (WebView2) on Windows: the page needs a current browser engine.
            browser = new Browser(parent, isWindows() ? SWT.EDGE : SWT.NONE);
        } catch (SWTError e) {
            Activator.error("No browser for the " + Brand.product() + " panel", e);
            message(parent, Brand.product() + " needs "
                    + (isWindows() ? "the Microsoft Edge WebView2 Runtime (installed with Windows 11 and Microsoft Edge)"
                            : "a web browser engine for Eclipse (on Linux: the libwebkit2gtk-4.1 package)")
                    + ". Install it, then restart Eclipse.\n\n" + e.getMessage());
            return null;
        }
        Panel panel = new Panel(browser, html.toUri().toString(), onMessage);
        panel.wire();
        return panel;
    }

    /**
     * Loads the page. Separate from {@link #create}: the caller registers the
     * panel first, because the page's "ready" can arrive at once (SWT's Edge
     * runs the event loop while it starts) and its answer must find the panel.
     */
    public void load() {
        browser.setUrl(pageUrl);
    }

    private static void message(Composite parent, String text) {
        Label label = new Label(parent, SWT.WRAP);
        label.setText(text);
        parent.layout();
    }

    private void wire() {
        // Messages from the page: only from our page (H19).
        new BrowserFunction(browser, "cmcoderHostPost") {
            @Override
            public Object function(Object[] arguments) {
                if (arguments.length != 1 || !(arguments[0] instanceof String)) return null;
                String url = browser.getUrl();
                if (url == null || !samePage(url)) return null;
                // The page's script runs (and says "ready") before the browser reports
                // the load complete: a message from it means it can take messages.
                loaded = true;
                try {
                    onMessage.accept((String) arguments[0]);
                } catch (RuntimeException e) {
                    Activator.error("A message from the panel failed", e);
                }
                return null;
            }
        };
        // Never leave the page; links go through openLink (H18).
        browser.addLocationListener(new LocationAdapter() {
            @Override
            public void changing(LocationEvent event) {
                if (!samePage(event.location)) event.doit = false;
            }
        });
        browser.addOpenWindowListener(event -> event.required = true); // no new windows
        browser.setMenu(new Menu(browser)); // no "view source"/"inspect" menu
        browser.addProgressListener(new ProgressAdapter() {
            @Override
            public void completed(ProgressEvent event) {
                loaded = true;
                applyTheme();
            }
        });
        PlatformUI.getWorkbench().getThemeManager().addPropertyChangeListener(themeListener);
        JFaceResources.getFontRegistry().addListener(themeListener);
        browser.addDisposeListener(e -> {
            PlatformUI.getWorkbench().getThemeManager().removePropertyChangeListener(themeListener);
            JFaceResources.getFontRegistry().removeListener(themeListener);
        });
    }

    private boolean samePage(String url) {
        return stripFragment(url).equalsIgnoreCase(stripFragment(pageUrl));
    }

    private static String stripFragment(String url) {
        int hash = url.indexOf('#');
        return hash < 0 ? url : url.substring(0, hash);
    }

    public Browser browser() {
        return browser;
    }

    public boolean disposed() {
        return browser.isDisposed();
    }

    /** A message (JSON) to the page; any thread. Dropped while the page loads: it asks again with "ready". */
    public void post(String json) {
        Display display = browser.getDisplay();
        if (display.isDisposed()) return;
        display.asyncExec(() -> {
            if (browser.isDisposed() || !loaded) return;
            // The JSON travels as a string literal and is parsed in the page.
            browser.execute("window.postMessage(JSON.parse(" + Json.write(json) + "),'*')");
        });
    }

    public void setFocus() {
        browser.setFocus();
    }

    // -- the page ---------------------------------------------------------------

    /** Writes {@code <page>.html} for this start of the plugin, in the plugin's own state folder. */
    static Path writePage(String page) throws IOException {
        Path panel = Activator.file("panel");
        if (panel == null || !Files.isRegularFile(panel.resolve(page + ".js"))) {
            throw new IOException("the plugin's panel files are missing (install the plugin again)");
        }
        String source = panel.toUri().toString().replaceAll("/$", "");
        String nonce = nonce();
        String testDriver = System.getProperty(TEST_DRIVER);
        String testScript = testDriver == null || testDriver.isBlank() ? ""
                : "\n  <script nonce=\"" + nonce + "\" src=\"" + attr(Path.of(testDriver).toUri().toString()) + "\"></script>";
        String icon = Files.isRegularFile(panel.resolve("icon.png")) ? " data-icon=\"" + attr(source + "/icon.png") + "\"" : "";
        // connect-src swt: is SWT's own channel for cmcoderHostPost on Linux (WebKitGTK
        // sends it as a request to swt://browserfunction/); nothing else can be fetched.
        String html = "<!DOCTYPE html>\n<html lang=\"en\"><head>\n"
                + "  <meta charset=\"UTF-8\">\n"
                + "  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src file:; img-src file: data:; connect-src swt:; script-src 'nonce-"
                + nonce + "'\">\n"
                + "  <link href=\"" + attr(source + "/" + page + ".css") + "\" rel=\"stylesheet\">\n"
                + "</head>\n<body data-product=\"" + attr(Brand.product()) + "\"" + icon + " data-host=\"function\">\n"
                + "  <div id=\"app\"></div>\n"
                + "  <script nonce=\"" + nonce + "\" src=\"" + attr(source + "/" + page + ".js") + "\"></script>" + testScript + "\n"
                + "</body></html>\n";
        Path folder = Activator.get().getStateLocation().toFile().toPath().resolve("pages");
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

    // -- the theme (H16) ------------------------------------------------------------

    /** Sets the page's CSS variables from Eclipse's colours and fonts (light or dark). */
    void applyTheme() {
        if (browser.isDisposed() || !loaded) return;
        Map<String, String> vars = themeVariables(browser.getParent().getBackground().getRGB(),
                browser.getParent().getForeground().getRGB());
        StringBuilder js = new StringBuilder("(function(){var s=document.documentElement.style;");
        for (Map.Entry<String, String> v : vars.entrySet()) {
            js.append("s.setProperty(").append(Json.write(v.getKey())).append(',').append(Json.write(v.getValue())).append(");");
        }
        js.append("})()");
        browser.execute(js.toString());
    }

    /** theme.json's defaults for a light or dark Eclipse, with its own background, text and fonts. */
    static Map<String, String> themeVariables(RGB background, RGB foreground) {
        boolean dark = luminance(background) < 0.5;
        Map<String, String> vars = new LinkedHashMap<>();
        Path themeFile = Activator.file("panel/theme.json");
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
                Activator.error("Could not read the panel's theme.json", e);
            }
        }
        String bg = hex(background);
        String fg = hex(foreground);
        vars.put("--vscode-sideBar-background", bg);
        vars.put("--vscode-foreground", fg);
        vars.put("--vscode-input-foreground", fg);
        vars.put("--vscode-button-secondaryForeground", fg);
        font(vars, "--vscode-font-family", "--vscode-font-size", JFaceResources.getDialogFont().getFontData(), "sans-serif");
        font(vars, "--vscode-editor-font-family", "--vscode-editor-font-size", JFaceResources.getTextFont().getFontData(), "monospace");
        return vars;
    }

    private static void font(Map<String, String> vars, String family, String size, FontData[] data, String fallback) {
        if (data == null || data.length == 0) return;
        vars.put(family, "\"" + data[0].getName().replace("\"", "") + "\", " + fallback);
        vars.put(size, data[0].getHeight() + "pt");
    }

    static double luminance(RGB c) {
        return (0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue) / 255.0;
    }

    static String hex(RGB c) {
        return String.format(Locale.ROOT, "#%02x%02x%02x", c.red, c.green, c.blue);
    }

    private static boolean isWindows() {
        return System.getProperty("os.name", "").toLowerCase(Locale.ROOT).startsWith("windows");
    }
}
