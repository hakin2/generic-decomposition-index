import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;

/** Stable identifier for an ordered experiment query set. */
public final class ExperimentFingerprint {
    private ExperimentFingerprint() {
    }

    public static String forVertices(String label, List<Integer> vertices) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(label.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) '\n');
            for (Integer vertex : vertices) {
                digest.update(Integer.toString(vertex).getBytes(StandardCharsets.UTF_8));
                digest.update((byte) '\n');
            }
            StringBuilder result = new StringBuilder();
            for (byte value : digest.digest()) {
                result.append(String.format("%02x", value & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
