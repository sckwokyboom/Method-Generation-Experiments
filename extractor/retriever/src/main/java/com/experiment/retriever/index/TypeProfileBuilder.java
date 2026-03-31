package com.experiment.retriever.index;

import com.experiment.shared.model.ClassField;
import com.experiment.shared.model.ExtractedMethod;

public final class TypeProfileBuilder {
    private TypeProfileBuilder() {}

    public static String build(ExtractedMethod method) {
        StringBuilder sb = new StringBuilder(256);

        // Return type
        appendSimpleName(sb, method.returnType());

        // Parameter types
        if (method.parameterTypes() != null) {
            for (String pt : method.parameterTypes()) {
                appendSimpleName(sb, pt);
            }
        }

        // Class fields types
        if (method.classFields() != null) {
            for (ClassField f : method.classFields()) {
                appendSimpleName(sb, f.typeFqn());
            }
        }

        // Used types in body
        if (method.usedTypes() != null) {
            for (String ut : method.usedTypes()) {
                appendSimpleName(sb, ut);
            }
        }

        // Class name itself
        appendSimpleName(sb, method.classFqn());

        // Supertypes
        if (method.supertypes() != null) {
            for (String st : method.supertypes()) {
                appendSimpleName(sb, st);
            }
        }

        return sb.toString().trim();
    }

    private static void appendSimpleName(StringBuilder sb, String fqn) {
        if (fqn == null || fqn.isBlank()) return;
        // Extract simple name: "com.example.Foo" -> "Foo"
        // Handle generics: "List<com.example.Foo>" -> "List Foo"
        for (String part : fqn.split("[<>,\\s]+")) {
            int dot = part.lastIndexOf('.');
            String simple = dot > 0 ? part.substring(dot + 1) : part;
            // Skip primitives and very short names
            if (simple.length() >= 3 && Character.isUpperCase(simple.charAt(0))) {
                sb.append(simple).append(' ');
            }
        }
    }
}
