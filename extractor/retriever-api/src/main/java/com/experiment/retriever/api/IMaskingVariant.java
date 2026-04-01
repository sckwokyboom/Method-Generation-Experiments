package com.experiment.retriever.api;

import java.util.Collection;

/**
 * Describes how source code is masked for a completion request.
 * The primary masking range defines the region the model should generate;
 * extra masking offsets identify additional regions that have been hidden.
 */
public interface IMaskingVariant {

    int getOffsetInMaskedSource();

    MaskingRange getMaskingRange();

    Collection<MaskingRange> getExtraMaskingOffsets();

    boolean isEmptyMaskedPart();
}
