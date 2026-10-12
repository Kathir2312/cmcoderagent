// The cmcoder plugin for IntelliJ-based IDEs (docs/phase7/PLAN.md).
//   gradle buildPlugin          the plugin zip (build/distributions/)
//   gradle verifyPlugin         JetBrains' Plugin Verifier: the oldest supported
//                               release, the newest IntelliJ IDEA and Rider
//   -PcmcoderDir=DIR            bundle that standalone cmcoder (the release build;
//                               without it the plugin finds cmcoder as the others do)
//   -Pgate                      the test build for the release gate (never shipped):
//                               the self-test (src/gate) and the page's test driver
//                               inside, the zip named ...-gate.zip (gate/run-gate.sh)
// The JVM core (clients/jvm-core, shared with Eclipse and NetBeans) is
// compiled into the plugin; the shared chat page (clients/web-panel/dist, built
// first with npm) and the brand's name and icon go into its panel/ folder.
// The brand (branding/) also names the plugin, its vendor and menus, and draws
// its icons (H24).

import groovy.json.JsonSlurper
import java.util.zip.ZipFile
import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.models.ProductRelease
import org.jetbrains.intellij.platform.gradle.tasks.VerifyPluginTask.FailureLevel

plugins {
    java
    id("org.jetbrains.intellij.platform") version "2.19.0"
}

group = "cmcoder"
version = providers.gradleProperty("pluginVersion").get()
val gate = providers.gradleProperty("gate").isPresent

// -- the brand (branding/brand.json, checked by packaging/brand.py) -----------------------
val brandFile = file("../branding/brand.json")
val brand = (JsonSlurper().parse(brandFile) as Map<*, *>)
fun brandValue(key: String, default: String): String =
    (brand[key] as? String)?.trim()?.takeIf { it.isNotEmpty() } ?: default
val productName = brandValue("productName", "cmcoder")
val vendorName = brandValue("company", "").ifEmpty { brandValue("publisher", "cmcoder") }
val accentColor = brandValue("accentColor", "#4F8EF7")
check(Regex("^#[0-9A-Fa-f]{6}$").matches(accentColor)) { "branding/brand.json: accentColor must look like #4F8EF7" }
fun xmlText(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")
// In menus "_" and "&" mark a shortcut letter: doubled, they are shown as they are.
val productXml = xmlText(productName)
val productInMenuXml = xmlText(productName.replace("_", "__").replace("&", "&&"))

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        intellijIdeaCommunity(providers.gradleProperty("platformVersion"))
        pluginVerifier()
    }
}

// Drawn from the brand: the plugin's icon (Settings → Plugins, 40×40, the
// accent colour), the tool window's and navigator tab's icon (16×16, the
// IDE's icon greys for light and dark themes); and the version Copy
// Diagnostics reports.
val brandResources = tasks.register("brandResources") {
    val source = file("../branding/icon-mono.svg")
    val out = layout.buildDirectory.dir("generated/brand")
    val accent = accentColor
    val pluginVersion = project.version.toString()
    inputs.file(source)
    inputs.property("accentColor", accent)
    inputs.property("version", pluginVersion)
    outputs.dir(out)
    doLast {
        val dir = out.get().asFile
        val svg = source.readText()
        val open = Regex("<svg\\b[^>]*>").find(svg) ?: error("branding/icon-mono.svg: no <svg> element")
        fun icon(path: String, size: Int, colour: String) {
            val tag = open.value.replace(Regex("\\s(width|height)=\"[^\"]*\""), "")
                .replaceFirst("<svg", "<svg width=\"$size\" height=\"$size\"")
            val text = svg.replaceRange(open.range, tag).replace(Regex("currentColor", RegexOption.IGNORE_CASE), colour)
            dir.resolve(path).apply { parentFile.mkdirs() }.writeText(text)
        }
        icon("META-INF/pluginIcon.svg", 40, accent)
        icon("icons/toolWindow.svg", 16, "#6C707E")
        icon("icons/toolWindow_dark.svg", 16, "#CED0D6")
        dir.resolve("cmcoder/idea/build.properties").apply { parentFile.mkdirs() }.writeText("version=$pluginVersion\n")
    }
}

sourceSets {
    main {
        java {
            srcDir("../clients/jvm-core/src/main/java")
            if (gate) srcDir("src/gate/java")
        }
        resources {
            srcDir(brandResources)
        }
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}

tasks.withType<JavaCompile>().configureEach {
    options.release = 21
    options.encoding = "UTF-8"
}

intellijPlatform {
    // No forms or @NotNull instrumentation needed; nothing else to download.
    instrumentCode = false
    buildSearchableOptions = false

    pluginConfiguration {
        name = productName
        version = providers.gradleProperty("pluginVersion")
        vendor {
            name = vendorName
        }
        ideaVersion {
            sinceBuild = providers.gradleProperty("sinceBuild")
            untilBuild = provider { null } // no upper bound unless the verifier finds a break
        }
    }

    pluginVerification {
        failureLevel = listOf(
            FailureLevel.COMPATIBILITY_PROBLEMS,
            FailureLevel.INTERNAL_API_USAGES,
            FailureLevel.SCHEDULED_FOR_REMOVAL_API_USAGES,
            FailureLevel.OVERRIDE_ONLY_API_USAGES,
            FailureLevel.NON_EXTENDABLE_API_USAGES,
            FailureLevel.MISSING_DEPENDENCIES,
            FailureLevel.INVALID_PLUGIN,
        )
        ides {
            // The oldest supported release (what the plugin is compiled against)...
            create(IntelliJPlatformType.IntellijIdeaCommunity, providers.gradleProperty("platformVersion"))
            // ...and the newest IntelliJ IDEA release...
            latest {
                types.set(listOf(IntelliJPlatformType.IntellijIdea))
                channels.set(listOf(ProductRelease.Channel.RELEASE))
            }
            // ...and the newest Rider, which the release gate can't start
            // without a license (gate/run-gate.sh runs IntelliJ IDEA and PyCharm).
            latest {
                types.set(listOf(IntelliJPlatformType.Rider))
                channels.set(listOf(ProductRelease.Channel.RELEASE))
            }
        }
    }
}

// plugin.xml's texts with the brand's name (ids keep "cmcoder"), in the
// patched plugin.xml: the one that goes into the jar.
tasks.patchPluginXml {
    inputs.property("productName", productName)
    val product = productXml
    val productInMenu = productInMenuXml
    doLast {
        val xml = outputFile.get().asFile
        xml.writeText(xml.readText().replace("@productInMenu@", productInMenu).replace("@product@", product))
    }
}

// What the IDE reads: plugin.xml in the jar, with no token left unfilled.
tasks.jar {
    val testBuild = gate
    doLast {
        ZipFile(archiveFile.get().asFile).use { zip ->
            val entry = zip.getEntry("META-INF/plugin.xml") ?: error("No META-INF/plugin.xml in the plugin's jar")
            val text = zip.getInputStream(entry).readBytes().toString(Charsets.UTF_8)
            check(!text.contains("@product")) { "plugin.xml: the brand's name wasn't filled in" }
            check(zip.getEntry("META-INF/pluginIcon.svg") != null) { "No plugin icon in the plugin's jar" }
            check(testBuild || zip.getEntry("cmcoder/idea/Gate.class") == null) { "The gate's self-test is in a release jar" }
        }
    }
}

// The gate's test build can't be mistaken for a release.
tasks.buildPlugin {
    if (gate) archiveClassifier = "gate"
}

// The plugin's folder, as the IDE unpacks it: lib/ (the code), panel/ (the
// chat's files), bin/cmcoder/ (the program, when bundled).
tasks.prepareSandbox {
    val panel = pluginName.map { "$it/panel" }
    from("../clients/web-panel/dist") {
        include("chat.js", "chat.css", "navigator.js", "navigator.css")
        into(panel)
    }
    from("../clients/web-panel/theme.json") { into(panel) }
    from("../branding") {
        include("brand.json", "icon.png")
        into(panel)
    }
    if (gate) {
        from("../clients/web-panel/dist") {
            include("test-driver.js")
            into(panel)
        }
    }
    providers.gradleProperty("cmcoderDir").orNull?.let { dir ->
        from(dir) { into(pluginName.map { "$it/bin/cmcoder" }) }
    }
    doLast {
        val chat = destinationDir.resolve("${pluginName.get()}/panel/chat.js")
        check(chat.isFile) { "No chat page in the plugin: build clients/web-panel first (npm ci && npm run build)" }
    }
}
