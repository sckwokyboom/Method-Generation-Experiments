package com.experiment.retriever.api;

import java.nio.file.Path;
import java.util.Objects;

/**
 * A retrieval request that carries masking information alongside the source code.
 * The offset is derived from the masking variant, representing the cursor position
 * inside the masked (to-be-generated) region.
 */
public class RetrieveRequestWithMaskingVariant implements IRetrieveRequest {

    private final String sourceCode;
    private final IMaskingVariant maskingVariant;
    private final String userPrompt;
    private final Path location;
    private final int topK;

    public RetrieveRequestWithMaskingVariant(
            String sourceCode,
            IMaskingVariant maskingVariant,
            String userPrompt,
            Path location) {
        this(sourceCode, maskingVariant, userPrompt, location, 0);
    }

    public RetrieveRequestWithMaskingVariant(
            String sourceCode,
            IMaskingVariant maskingVariant,
            String userPrompt,
            Path location,
            int topK) {
        this.sourceCode = Objects.requireNonNull(sourceCode, "sourceCode");
        this.maskingVariant = Objects.requireNonNull(maskingVariant, "maskingVariant");
        this.userPrompt = userPrompt != null ? userPrompt : "";
        this.location = Objects.requireNonNull(location, "location");
        this.topK = topK;
    }

    @Override
    public String getSourceCode() {
        return sourceCode;
    }

    @Override
    public int getOffset() {
        return maskingVariant.getOffsetInMaskedSource();
    }

    @Override
    public String getUserPrompt() {
        return userPrompt;
    }

    @Override
    public Path getLocation() {
        return location;
    }

    @Override
    public int getTopK() {
        return topK;
    }

    public IMaskingVariant getMaskingVariant() {
        return maskingVariant;
    }
}
