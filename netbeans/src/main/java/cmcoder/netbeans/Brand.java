package cmcoder.netbeans;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import cmcoder.ide.core.Json;

/** The company's product name (branding/brand.json, copied into the module's panel/ at build time). */
public final class Brand {
    private static String product;

    private Brand() {}

    public static synchronized String product() {
        if (product == null) {
            product = "cmcoder";
            Path file = Plugin.file("panel/brand.json");
            if (file != null) {
                try {
                    Map<String, Object> brand = Json.parseObject(Files.readString(file, StandardCharsets.UTF_8));
                    String name = Json.string(brand, "productName");
                    if (name != null && !name.isBlank()) product = name.trim();
                } catch (IOException | RuntimeException e) {
                    // the default name
                }
            }
        }
        return product;
    }
}
