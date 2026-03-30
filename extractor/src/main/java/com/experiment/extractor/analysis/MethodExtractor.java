package com.experiment.extractor.analysis;

import com.experiment.extractor.classpath.ClasspathResolver;
import com.experiment.extractor.model.ExtractionMeta;
import com.experiment.extractor.model.ExtractionResult;
import com.experiment.extractor.model.ExtractedMethod;
import com.experiment.extractor.model.MethodCategory;
import com.experiment.extractor.model.ResolvedInvocation;
import org.eclipse.jdt.core.JavaCore;
import org.eclipse.jdt.core.dom.AST;
import org.eclipse.jdt.core.dom.ASTParser;
import org.eclipse.jdt.core.dom.ASTVisitor;
import org.eclipse.jdt.core.dom.Block;
import org.eclipse.jdt.core.dom.ClassInstanceCreation;
import org.eclipse.jdt.core.dom.CompilationUnit;
import org.eclipse.jdt.core.dom.ConstructorInvocation;
import org.eclipse.jdt.core.dom.IMethodBinding;
import org.eclipse.jdt.core.dom.ITypeBinding;
import org.eclipse.jdt.core.dom.MethodDeclaration;
import org.eclipse.jdt.core.dom.MethodInvocation;
import org.eclipse.jdt.core.dom.SingleVariableDeclaration;
import org.eclipse.jdt.core.dom.SuperConstructorInvocation;
import org.eclipse.jdt.core.dom.SuperMethodInvocation;
import org.eclipse.jdt.core.dom.TypeDeclaration;
import org.eclipse.jdt.core.dom.EnumDeclaration;
import org.eclipse.jdt.core.dom.AbstractTypeDeclaration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Stream;

public class MethodExtractor {
    private static final Logger log = LoggerFactory.getLogger(MethodExtractor.class);

    private final Path projectPath;
    private final int minStatements;
    private final boolean includeTests;

    public MethodExtractor(Path projectPath, int minStatements, boolean includeTests) {
        this.projectPath = projectPath.toAbsolutePath().normalize();
        this.minStatements = minStatements;
        this.includeTests = includeTests;
    }

    public ExtractionResult extract() {
        long startMs = System.currentTimeMillis();
        String projectName = projectPath.getFileName().toString();

        ClasspathResolver resolver = new ClasspathResolver();
        ClasspathResolver.Result cpResult = resolver.resolve(projectPath);
        for (String w : cpResult.warnings()) {
            log.warn("Classpath warning: {}", w);
        }

        String[] classpath = cpResult.classpathEntries().toArray(String[]::new);
        String[] sourceRoots = cpResult.sourceRoots().toArray(String[]::new);

        List<Path> javaSources = findJavaSources();
        log.info("Found {} Java source files", javaSources.size());

        List<ExtractedMethod> allMethods = new ArrayList<>();
        int totalMethods = 0;
        int unresolvedInvocations = 0;

        for (Path sourceFile : javaSources) {
            try {
                String source = Files.readString(sourceFile, StandardCharsets.UTF_8);
                String relativePath = projectPath.relativize(sourceFile).toString();
                boolean isTestSource = relativePath.replace('\\', '/').contains("/test/");

                CompilationUnit cu = parseFile(source, sourceFile, classpath, sourceRoots);

                List<ExtractedMethod> fileMethods = extractMethods(cu, source, relativePath, isTestSource);
                totalMethods += fileMethods.size();

                for (ExtractedMethod m : fileMethods) {
                    for (ResolvedInvocation inv : m.invocations()) {
                        if ("UNRESOLVED".equals(inv.resolutionMode())) {
                            unresolvedInvocations++;
                        }
                    }
                }

                allMethods.addAll(fileMethods);
            } catch (IOException e) {
                log.warn("Failed to read file: {}", sourceFile, e);
            }
        }

        long durationMs = System.currentTimeMillis() - startMs;
        ExtractionMeta meta = new ExtractionMeta(
                Instant.now().toString(),
                durationMs,
                javaSources.size(),
                totalMethods,
                allMethods.size(),
                unresolvedInvocations
        );

        log.info("Extraction complete: {} files, {} total methods, {} extracted, {} unresolved invocations",
                javaSources.size(), totalMethods, allMethods.size(), unresolvedInvocations);

        return new ExtractionResult(projectName, meta, cpResult.classpathEntries(), allMethods);
    }

    private List<Path> findJavaSources() {
        List<Path> sources = new ArrayList<>();
        try (Stream<Path> stream = Files.walk(projectPath)) {
            stream.filter(p -> p.toString().endsWith(".java"))
                    .filter(Files::isRegularFile)
                    .filter(p -> {
                        String normalized = p.toString().replace('\\', '/');
                        if (!includeTests && normalized.contains("/src/test/")) return false;
                        if (normalized.contains("/build/")) return false;
                        if (normalized.contains("/.gradle/")) return false;
                        return true;
                    })
                    .forEach(sources::add);
        } catch (IOException e) {
            log.error("Failed to walk project directory: {}", projectPath, e);
        }
        return sources;
    }

    private CompilationUnit parseFile(String source, Path sourceFile, String[] classpath, String[] sourceRoots) {
        ASTParser parser = ASTParser.newParser(AST.getJLSLatest());
        parser.setKind(ASTParser.K_COMPILATION_UNIT);
        parser.setSource(source.toCharArray());
        parser.setResolveBindings(true);
        parser.setBindingsRecovery(true);
        parser.setStatementsRecovery(true);
        parser.setEnvironment(classpath, sourceRoots, null, true);
        parser.setCompilerOptions(compilerOptions());
        parser.setUnitName(projectPath.relativize(sourceFile).toString());
        return (CompilationUnit) parser.createAST(null);
    }

    @SuppressWarnings("unchecked")
    private Map<String, String> compilerOptions() {
        Map<String, String> options = JavaCore.getOptions();
        options.put(JavaCore.COMPILER_SOURCE, "21");
        options.put(JavaCore.COMPILER_COMPLIANCE, "21");
        options.put(JavaCore.COMPILER_CODEGEN_TARGET_PLATFORM, "21");
        return options;
    }

    private List<ExtractedMethod> extractMethods(CompilationUnit cu, String source, String filePath, boolean isTestSource) {
        List<ExtractedMethod> methods = new ArrayList<>();
        Deque<String> typeStack = new ArrayDeque<>();

        cu.accept(new ASTVisitor() {
            @Override
            public boolean visit(TypeDeclaration node) {
                typeStack.addLast(resolveTypeFqn(node));
                return true;
            }

            @Override
            public void endVisit(TypeDeclaration node) {
                if (!typeStack.isEmpty()) typeStack.removeLast();
            }

            @Override
            public boolean visit(EnumDeclaration node) {
                typeStack.addLast(resolveEnumFqn(node));
                return true;
            }

            @Override
            public void endVisit(EnumDeclaration node) {
                if (!typeStack.isEmpty()) typeStack.removeLast();
            }

            @Override
            public boolean visit(MethodDeclaration node) {
                Block body = node.getBody();
                if (body == null) return false;

                String classFqn = typeStack.isEmpty() ? "UNKNOWN" : typeStack.peekLast();

                MethodCategory category = MethodClassifier.classify(node, isTestSource);
                int stmtCount = body.statements().size();

                if (stmtCount < minStatements && category == MethodCategory.NORMAL) {
                    return false;
                }
                if (category != MethodCategory.NORMAL) {
                    return false;
                }

                int bodyStart = body.getStartPosition();
                int bodyEnd = bodyStart + body.getLength();

                String methodBody = source.substring(bodyStart, bodyEnd);
                String methodSig = buildMethodSignature(node);

                List<ResolvedInvocation> invocations = extractInvocations(body, cu);

                ExtractedMethod extracted = new ExtractedMethod(
                        filePath, source, classFqn, methodSig,
                        node.getName().getIdentifier(), methodBody,
                        bodyStart, bodyEnd, stmtCount, category, invocations
                );

                methods.add(extracted);
                return false;
            }
        });

        return methods;
    }

    private String resolveTypeFqn(TypeDeclaration node) {
        ITypeBinding binding = node.resolveBinding();
        if (binding != null) return binding.getQualifiedName();
        return node.getName().getIdentifier();
    }

    private String resolveEnumFqn(EnumDeclaration node) {
        ITypeBinding binding = node.resolveBinding();
        if (binding != null) return binding.getQualifiedName();
        return node.getName().getIdentifier();
    }

    @SuppressWarnings("unchecked")
    private String buildMethodSignature(MethodDeclaration node) {
        StringBuilder sb = new StringBuilder();

        node.modifiers().stream()
                .filter(m -> m instanceof org.eclipse.jdt.core.dom.Modifier)
                .forEach(m -> sb.append(m).append(' '));

        if (node.getReturnType2() != null) {
            sb.append(node.getReturnType2()).append(' ');
        }

        sb.append(node.getName().getIdentifier()).append('(');
        List<SingleVariableDeclaration> params = node.parameters();
        for (int i = 0; i < params.size(); i++) {
            if (i > 0) sb.append(", ");
            SingleVariableDeclaration param = params.get(i);
            sb.append(param.getType());
            if (param.isVarargs()) sb.append("...");
            sb.append(' ').append(param.getName());
        }
        sb.append(')');

        return sb.toString();
    }

    private List<ResolvedInvocation> extractInvocations(Block body, CompilationUnit cu) {
        List<ResolvedInvocation> invocations = new ArrayList<>();
        int[] orderCounter = {0};

        body.accept(new ASTVisitor() {
            @Override
            public boolean visit(MethodInvocation node) {
                addInvocation(node.resolveMethodBinding(), node.getName().getIdentifier(), orderCounter[0]++);
                return true;
            }

            @Override
            public boolean visit(SuperMethodInvocation node) {
                addInvocation(node.resolveMethodBinding(), node.getName().getIdentifier(), orderCounter[0]++);
                return true;
            }

            @Override
            public boolean visit(ClassInstanceCreation node) {
                IMethodBinding binding = node.resolveConstructorBinding();
                String name = node.getType().toString();
                addInvocation(binding, "<init>(" + name + ")", orderCounter[0]++);
                return true;
            }

            @Override
            public boolean visit(ConstructorInvocation node) {
                addInvocation(node.resolveConstructorBinding(), "this", orderCounter[0]++);
                return true;
            }

            @Override
            public boolean visit(SuperConstructorInvocation node) {
                addInvocation(node.resolveConstructorBinding(), "super", orderCounter[0]++);
                return true;
            }

            private void addInvocation(IMethodBinding binding, String fallbackName, int order) {
                if (binding != null) {
                    String signature = formatSignature(binding);
                    invocations.add(new ResolvedInvocation(signature, "EXACT", order));
                } else {
                    String signature = "UNRESOLVED<" + fallbackName + ">";
                    invocations.add(new ResolvedInvocation(signature, "UNRESOLVED", order));
                }
            }
        });

        return invocations;
    }

    private String formatSignature(IMethodBinding binding) {
        IMethodBinding original = binding.getMethodDeclaration();

        String declaringClass = original.getDeclaringClass() != null
                ? original.getDeclaringClass().getQualifiedName()
                : "UNKNOWN";

        String methodName = original.isConstructor() ? "<init>" : original.getName();

        StringBuilder params = new StringBuilder();
        ITypeBinding[] paramTypes = original.getParameterTypes();
        for (int i = 0; i < paramTypes.length; i++) {
            if (i > 0) params.append(", ");
            params.append(formatType(paramTypes[i]));
        }

        String returnType = original.isConstructor()
                ? declaringClass
                : formatType(original.getReturnType());

        return declaringClass + "::" + methodName + "(" + params + ") -> " + returnType;
    }

    private String formatType(ITypeBinding type) {
        if (type == null) return "UNKNOWN";
        if (type.isArray()) {
            return formatType(type.getElementType()) + "[]".repeat(type.getDimensions());
        }
        if (type.isPrimitive()) {
            return type.getName();
        }
        String qname = type.getQualifiedName();
        return qname.isEmpty() ? type.getName() : qname;
    }
}
