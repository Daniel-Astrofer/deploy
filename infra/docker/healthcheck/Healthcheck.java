import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public class Healthcheck {

    public static void main(String[] args) throws Exception {
        if (args.length >= 2 && "--sleep-seconds".equals(args[0])) {
            long seconds = Long.parseLong(args[1]);
            if (seconds < 0 || seconds > 300) {
                throw new IllegalArgumentException("sleep seconds must be between 0 and 300");
            }
            Thread.sleep(Duration.ofSeconds(seconds).toMillis());
            return;
        }

        String target = args.length > 0 ? args[0] : "http://127.0.0.1:8080/health/ready";
        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();
        HttpRequest request = HttpRequest.newBuilder(URI.create(target))
                .timeout(Duration.ofSeconds(3))
                .GET()
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        int status = response.statusCode();
        if (status >= 200 && status < 300 && response.body().contains("\"status\":\"UP\"")) {
            return;
        }
        System.err.println("Healthcheck failed with HTTP " + status + ": " + response.body());
        System.exit(1);
    }
}
