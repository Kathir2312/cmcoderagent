// The cmcoder plugin for IntelliJ-based IDEs (docs/phase7/PLAN.md).
//   gradle buildPlugin          the plugin zip (build/distributions/)
//   gradle verifyPlugin         JetBrains' Plugin Verifier: the oldest supported
//                               release and the newest IntelliJ IDEA
// The JVM core (clients/jvm-core, shared with Eclipse and NetBeans) is
// compiled into the plugin.

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
