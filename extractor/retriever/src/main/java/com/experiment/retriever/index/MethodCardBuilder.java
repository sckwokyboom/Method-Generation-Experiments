package com.experiment.retriever.index;

import com.experiment.shared.model.ClassField;
import com.experiment.shared.model.ExtractedMethod;
import com.experiment.shared.model.ResolvedInvocation;

public final class MethodCardBuilder {
    private MethodCardBuilder() {}

    public static String build(ExtractedMethod method) {
        StringBuilder sb = new StringBuilder(1024);

        // Package
        String classFqn = method.classFqn();
        int lastDot = classFqn.lastIndexOf('.');
        if (lastDot > 0) {
            sb.append("package: ").append(classFqn, 0, lastDot).append('\n');
        }

        // Class name
        String className = lastDot > 0 ? classFqn.substring(lastDot + 1) : classFqn;
        sb.append("class: ").append(className).append('\n');

        // Method signature (repeated for emphasis)
        sb.append("signature: ").append(method.methodSignature()).append('\n');
        sb.append("signature: ").append(method.methodSignature()).append('\n');

        // Return type
        if (method.returnType() != null) {
            sb.append("return: ").append(method.returnType()).append('\n');
        }

        // Parameter types
        if (method.parameterTypes() != null && !method.parameterTypes().isEmpty()) {
            sb.append("params: ").append(String.join(", ", method.parameterTypes())).append('\n');
        }

        // Thrown exceptions
        if (method.thrownExceptions() != null && !method.thrownExceptions().isEmpty()) {
            sb.append("throws: ").append(String.join(", ", method.thrownExceptions())).append('\n');
        }

        // Imports
        if (method.imports() != null && !method.imports().isEmpty()) {
            sb.append("imports: ").append(String.join(", ", method.imports())).append('\n');
        }

        // Class fields
        if (method.classFields() != null) {
            for (ClassField field : method.classFields()) {
                sb.append("field: ").append(field.typeFqn()).append(' ').append(field.name()).append('\n');
            }
        }

        // Invocation signatures
        if (method.invocations() != null) {
            for (ResolvedInvocation inv : method.invocations()) {
                if ("EXACT".equals(inv.resolutionMode())) {
                    sb.append("invocation: ").append(inv.signature()).append('\n');
                }
            }
        }

        // Used types
        if (method.usedTypes() != null && !method.usedTypes().isEmpty()) {
            sb.append("types: ").append(String.join(", ", method.usedTypes())).append('\n');
        }

        return sb.toString();
    }
}
