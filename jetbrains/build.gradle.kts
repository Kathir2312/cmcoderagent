// The cmcoder plugin for IntelliJ-based IDEs (docs/phase7/PLAN.md).
//   gradle buildPlugin          the plugin zip (build/distributions/)
//   gradle verifyPlugin         JetBrains' Plugin Verifier: the oldest supported
//                               release and the newest IntelliJ IDEA
//   -PcmcoderDir=DIR            bundle that standalone cmcoder (the release build;
//                               without it the plugin finds cmcoder as the others do)
// The JVM core (clients/jvm-core, shared with Eclipse and NetBeans) is
// compiled into the plugin; the shared chat page (clients/web-panel/dist, built
// first with npm) and the brand's name and icon go into its panel/ folder.

import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.models.ProductRelease
import org.jetbrains.intellij.platform.gradle.tasks.VerifyPluginTask.FailureLevel

plugins {
    java
    id("org.jetbrains.intellij.platform") version "2.19.0"
}

group = "cmcoder"
version = providers.gradleProperty("pluginVersion").get()

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

sourceSets {
    main {
        java {
            srcDir("../clients/jvm-core/src/main/java")
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
        version = providers.gradleProperty("pluginVersion")
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
            // ...and the newest IntelliJ IDEA release.
            latest {
                types.set(listOf(IntelliJPlatformType.IntellijIdea))
                channels.set(listOf(ProductRelease.Channel.RELEASE))
            }
        }
    }
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
    providers.gradleProperty("cmcoderDir").orNull?.let { dir ->
        from(dir) { into(pluginName.map { "$it/bin/cmcoder" }) }
    }
    doLast {
        val chat = destinationDir.resolve("${pluginName.get()}/panel/chat.js")
        check(chat.isFile) { "No chat page in the plugin: build clients/web-panel first (npm ci && npm run build)" }
    }
}
