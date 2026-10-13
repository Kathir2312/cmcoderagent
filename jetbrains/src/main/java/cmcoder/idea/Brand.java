package cmcoder.idea;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import cmcoder.ide.core.Json;

/** The company's product name (branding/brand.json, copied into the plugin's panel/ at build time). */
public final class Brand {
    private Brand() {}

    public static String product() {
        return Name.PRODUCT;
    }

    /** Read once, on first use (the JVM guards a class's initialisation: no lock of ours needed). */
    private static final class Name {
        static final String PRODUCT = read();

        private static String read() {
            try {
                Path file = Plugin.file("panel/brand.json");
                if (file != null) {
                    Map<String, Object> brand = Json.parseObject(Files.readString(file, StandardCharsets.UTF_8));
                    String name = Json.string(brand, "productName");
                    if (name != null && !name.isBlank()) return name.trim();
                }
            } catch (IOException | RuntimeException e) {
                // the default name
            }
            return "cmcoder";
        }
    }
}
