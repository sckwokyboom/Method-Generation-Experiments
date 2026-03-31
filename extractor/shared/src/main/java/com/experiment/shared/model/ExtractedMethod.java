package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractedMethod(
        @JsonProperty("filePath") String filePath,
        @JsonProperty("fileContent") String fileContent,
        @JsonProperty("classFqn") String classFqn,
        @JsonProperty("methodSignature") String methodSignature,
        @JsonProperty("methodName") String methodName,
        @JsonProperty("methodBody") String methodBody,
        @JsonProperty("bodyStartOffset") int bodyStartOffset,
        @JsonProperty("bodyEndOffset") int bodyEndOffset,
        @JsonProperty("statementCount") int statementCount,
        @JsonProperty("category") MethodCategory category,
        @JsonProperty("invocations") List<ResolvedInvocation> invocations,
        @JsonProperty("imports") List<String> imports,
        @JsonProperty("classFields") List<ClassField> classFields,
        @JsonProperty("supertypes") List<String> supertypes,
        @JsonProperty("siblingMethods") List<SiblingMethod> siblingMethods,
        @JsonProperty("returnType") String returnType,
        @JsonProperty("parameterTypes") List<String> parameterTypes,
        @JsonProperty("thrownExceptions") List<String> thrownExceptions,
        @JsonProperty("usedTypes") List<String> usedTypes
) {
    public ExtractedMethod(
            String filePath, String fileContent, String classFqn,
            String methodSignature, String methodName, String methodBody,
            int bodyStartOffset, int bodyEndOffset, int statementCount,
            MethodCategory category, List<ResolvedInvocation> invocations
    ) {
        this(filePath, fileContent, classFqn, methodSignature, methodName,
                methodBody, bodyStartOffset, bodyEndOffset, statementCount,
                category, invocations,
                List.of(), List.of(), List.of(), List.of(),
                null, List.of(), List.of(), List.of());
    }
}
