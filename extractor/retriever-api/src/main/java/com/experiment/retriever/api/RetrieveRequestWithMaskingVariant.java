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

    public RetrieveRequestWithMaskingVariant(
            String sourceCode,
            IMaskingVariant maskingVariant,
            String userPrompt,
            Path location) {
        this.sourceCode = Objects.requireNonNull(sourceCode, "sourceCode");
        this.maskingVariant = Objects.requireNonNull(maskingVariant, "maskingVariant");
        this.userPrompt = userPrompt != null ? userPrompt : "";
        this.location = Objects.requireNonNull(location, "location");
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

    public IMaskingVariant getMaskingVariant() {
        return maskingVariant;
    }
}
