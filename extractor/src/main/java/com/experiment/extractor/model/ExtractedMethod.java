package com.experiment.extractor.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

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
        @JsonProperty("invocations") List<ResolvedInvocation> invocations
) {}
