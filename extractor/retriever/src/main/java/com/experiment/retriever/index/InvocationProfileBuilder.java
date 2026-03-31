package com.experiment.retriever.index;

import com.experiment.shared.model.ResolvedInvocation;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class InvocationProfileBuilder {
    private InvocationProfileBuilder() {}

    public static String build(List<ResolvedInvocation> invocations) {
        if (invocations == null || invocations.isEmpty()) return "";

        List<ResolvedInvocation> sorted = invocations.stream()
                .filter(inv -> "EXACT".equals(inv.resolutionMode()))
                .sorted(Comparator.comparingInt(ResolvedInvocation::orderIndex))
                .toList();

        if (sorted.isEmpty()) return "";

        StringBuilder sb = new StringBuilder(512);

        // Individual invocation signatures
        Map<String, Integer> frequency = new LinkedHashMap<>();
        for (ResolvedInvocation inv : sorted) {
            frequency.merge(inv.signature(), 1, Integer::sum);
        }

        for (Map.Entry<String, Integer> entry : frequency.entrySet()) {
            sb.append(entry.getKey());
            if (entry.getValue() > 1) {
                sb.append(" x").append(entry.getValue());
            }
            sb.append('\n');
        }

        // Call bigrams
        for (int i = 0; i < sorted.size() - 1; i++) {
            String a = extractShortName(sorted.get(i).signature());
            String b = extractShortName(sorted.get(i + 1).signature());
            sb.append("bigram: ").append(a).append(" -> ").append(b).append('\n');
        }

        // Call trigrams
        for (int i = 0; i < sorted.size() - 2; i++) {
            String a = extractShortName(sorted.get(i).signature());
            String b = extractShortName(sorted.get(i + 1).signature());
            String c = extractShortName(sorted.get(i + 2).signature());
            sb.append("trigram: ").append(a).append(" -> ").append(b).append(" -> ").append(c).append('\n');
        }

        return sb.toString();
    }

    private static String extractShortName(String signature) {
        // "com.example.Foo::bar(int, String) -> void" => "Foo::bar"
        int parenIdx = signature.indexOf('(');
        String prefix = parenIdx > 0 ? signature.substring(0, parenIdx) : signature;
        int lastDot = prefix.lastIndexOf('.', prefix.indexOf("::"));
        return lastDot > 0 ? prefix.substring(lastDot + 1) : prefix;
    }
}
