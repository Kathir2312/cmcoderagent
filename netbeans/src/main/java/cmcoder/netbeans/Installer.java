package cmcoder.netbeans;

import org.openide.modules.ModuleInstall;

/** The module's start and end: cmcoder never outlives NetBeans (H3); the gate's self-test in test builds. */
public final class Installer extends ModuleInstall {
    private static final long serialVersionUID = 1L;

    @Override
    public void restored() {
        String gate = System.getProperty("cmcoder.gate");
        if (gate == null || gate.isBlank() || !Panel.testBuild()) return;
        try {
            // Only in the test build (-Pgate): never in a release.
            Class.forName("cmcoder.netbeans.Gate").getMethod("start", String.class).invoke(null, gate);
        } catch (ReflectiveOperationException e) {
            Plugin.error("cmcoder.gate is set, but this isn't the test build", e);
        }
    }

    @Override
    public void close() {
        Session.disposeAll();
    }
}
