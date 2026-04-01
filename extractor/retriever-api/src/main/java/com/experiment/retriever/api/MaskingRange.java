package com.experiment.retriever.api;

/**
 * A character offset range defining a masked (hidden) region in source code.
 *
 * @param maskingStartOffset inclusive start offset
 * @param maskingEndOffset   exclusive end offset
 */
public record MaskingRange(int maskingStartOffset, int maskingEndOffset) {

    public MaskingRange {
        if (maskingStartOffset < 0) {
            throw new IllegalArgumentException("maskingStartOffset must be non-negative");
        }
        if (maskingEndOffset < maskingStartOffset) {
            throw new IllegalArgumentException(
                    "maskingEndOffset (" + maskingEndOffset + ") must be >= maskingStartOffset (" + maskingStartOffset + ")");
        }
    }

    public int length() {
        return maskingEndOffset - maskingStartOffset;
    }
}
