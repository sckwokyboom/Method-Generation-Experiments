package com.experiment.extractor.analysis;

import com.experiment.extractor.classpath.ClasspathResolver;
import com.experiment.shared.model.*;
import org.eclipse.jdt.core.JavaCore;
import org.eclipse.jdt.core.dom.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.*;
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

    @SuppressWarnings("unchecked")
    private List<String> extractImports(CompilationUnit cu) {
        List<String> imports = new ArrayList<>();
        for (Object obj : cu.imports()) {
            ImportDeclaration imp = (ImportDeclaration) obj;
            imports.add(imp.getName().getFullyQualifiedName());
        }
        return imports;
    }

    @SuppressWarnings("unchecked")
    private List<ClassField> extractClassFields(TypeDeclaration typeDecl) {
        List<ClassField> fields = new ArrayList<>();
        for (FieldDeclaration field : typeDecl.getFields()) {
            String typeFqn = resolveTypeString(field.getType());
            for (Object fragObj : field.fragments()) {
                VariableDeclarationFragment frag = (VariableDeclarationFragment) fragObj;
                fields.add(new ClassField(frag.getName().getIdentifier(), typeFqn));
            }
        }
        return fields;
    }

    private List<ClassField> extractEnumFields(EnumDeclaration enumDecl) {
        // Enums have no getFields() on EnumDeclaration directly; body declarations can contain them
        List<ClassField> fields = new ArrayList<>();
        for (Object bodyDecl : enumDecl.bodyDeclarations()) {
            if (bodyDecl instanceof FieldDeclaration field) {
                String typeFqn = resolveTypeString(field.getType());
                for (Object fragObj : field.fragments()) {
                    VariableDeclarationFragment frag = (VariableDeclarationFragment) fragObj;
                    fields.add(new ClassField(frag.getName().getIdentifier(), typeFqn));
                }
            }
        }
        return fields;
    }

    @SuppressWarnings("unchecked")
    private List<String> extractSupertypes(TypeDeclaration typeDecl) {
        List<String> supertypes = new ArrayList<>();
        if (typeDecl.getSuperclassType() != null) {
            supertypes.add(resolveTypeString(typeDecl.getSuperclassType()));
        }
        for (Object iface : typeDecl.superInterfaceTypes()) {
            supertypes.add(resolveTypeString((Type) iface));
        }
        return supertypes;
    }

    @SuppressWarnings("unchecked")
    private List<String> extractEnumSupertypes(EnumDeclaration enumDecl) {
        List<String> supertypes = new ArrayList<>();
        for (Object iface : enumDecl.superInterfaceTypes()) {
            supertypes.add(resolveTypeString((Type) iface));
        }
        return supertypes;
    }

    private String resolveTypeString(Type type) {
        if (type == null) return "UNKNOWN";
        ITypeBinding binding = type.resolveBinding();
        if (binding != null) {
            String qname = binding.getQualifiedName();
            return qname.isEmpty() ? type.toString() : qname;
        }
        return type.toString();
    }

    private List<String> extractUsedTypes(Block body) {
        Set<String> types = new LinkedHashSet<>();
        body.accept(new ASTVisitor() {
            @Override
            public boolean visit(SimpleType node) {
                ITypeBinding binding = node.resolveBinding();
                if (binding != null) {
                    String qname = binding.getQualifiedName();
                    if (!qname.isEmpty() && !binding.isPrimitive()) {
                        types.add(qname);
                    }
                } else {
                    types.add(node.getName().getFullyQualifiedName());
                }
                return true;
            }

            @Override
            public boolean visit(ClassInstanceCreation node) {
                ITypeBinding binding = node.getType().resolveBinding();
                if (binding != null) {
                    String qname = binding.getQualifiedName();
                    if (!qname.isEmpty()) types.add(qname);
                }
                return true;
            }
        });
        return new ArrayList<>(types);
    }

    @SuppressWarnings("unchecked")
    private String resolveReturnType(MethodDeclaration node) {
        Type returnType = node.getReturnType2();
        if (returnType == null) return null;
        ITypeBinding binding = returnType.resolveBinding();
        if (binding != null) {
            return formatType(binding);
        }
        return returnType.toString();
    }

    @SuppressWarnings("unchecked")
    private List<String> resolveParameterTypes(MethodDeclaration node) {
        List<String> paramTypes = new ArrayList<>();
        List<SingleVariableDeclaration> params = node.parameters();
        for (SingleVariableDeclaration param : params) {
            ITypeBinding binding = param.getType().resolveBinding();
            if (binding != null) {
                paramTypes.add(formatType(binding));
            } else {
                paramTypes.add(param.getType().toString());
            }
        }
        return paramTypes;
    }

    @SuppressWarnings("unchecked")
    private List<String> resolveThrownExceptions(MethodDeclaration node) {
        List<String> thrown = new ArrayList<>();
        for (Object exType : node.thrownExceptionTypes()) {
            Type t = (Type) exType;
            thrown.add(resolveTypeString(t));
        }
        return thrown;
    }

    private record MethodInfo(
            MethodDeclaration declaration,
            String classFqn,
            MethodCategory category,
            int stmtCount,
            int bodyStart,
            int bodyEnd,
            String methodBody,
            String methodSig,
            List<ResolvedInvocation> invocations,
            List<String> usedTypes,
            String returnType,
            List<String> parameterTypes,
            List<String> thrownExceptions
    ) {}

    private List<ExtractedMethod> extractMethods(CompilationUnit cu, String source, String filePath, boolean isTestSource) {
        List<String> imports = extractImports(cu);
        List<ExtractedMethod> result = new ArrayList<>();
        Deque<String> typeStack = new ArrayDeque<>();

        cu.accept(new ASTVisitor() {
            @Override
            public boolean visit(TypeDeclaration node) {
                String fqn = resolveTypeFqn(node);
                typeStack.addLast(fqn);

                List<ClassField> classFields = extractClassFields(node);
                List<String> supertypes = extractSupertypes(node);

                processTypeMembers(node.getMethods(), fqn, classFields, supertypes,
                        source, filePath, isTestSource, cu, imports, result);
                return true;
            }

            @Override
            public void endVisit(TypeDeclaration node) {
                if (!typeStack.isEmpty()) typeStack.removeLast();
            }

            @Override
            public boolean visit(EnumDeclaration node) {
                String fqn = resolveEnumFqn(node);
                typeStack.addLast(fqn);

                List<ClassField> classFields = extractEnumFields(node);
                List<String> supertypes = extractEnumSupertypes(node);

                // Collect methods from enum body declarations
                List<MethodDeclaration> enumMethods = new ArrayList<>();
                for (Object bodyDecl : node.bodyDeclarations()) {
                    if (bodyDecl instanceof MethodDeclaration md) {
                        enumMethods.add(md);
                    }
                }
                processTypeMembers(enumMethods.toArray(new MethodDeclaration[0]),
                        fqn, classFields, supertypes, source, filePath, isTestSource, cu, imports, result);
                return true;
            }

            @Override
            public void endVisit(EnumDeclaration node) {
                if (!typeStack.isEmpty()) typeStack.removeLast();
            }
        });

        return result;
    }

    private void processTypeMembers(
            MethodDeclaration[] methodDecls,
            String classFqn,
            List<ClassField> classFields,
            List<String> supertypes,
            String source,
            String filePath,
            boolean isTestSource,
            CompilationUnit cu,
            List<String> imports,
            List<ExtractedMethod> result
    ) {
        // First pass: collect info for all methods (needed for sibling data)
        List<MethodInfo> allInfos = new ArrayList<>();
        for (MethodDeclaration node : methodDecls) {
            Block body = node.getBody();
            if (body == null) continue;

            MethodCategory category = MethodClassifier.classify(node, isTestSource);
            int stmtCount = body.statements().size();

            int bodyStart = body.getStartPosition();
            int bodyEnd = bodyStart + body.getLength();
            String methodBody = source.substring(bodyStart, bodyEnd);
            String methodSig = buildMethodSignature(node);
            List<ResolvedInvocation> invocations = extractInvocations(body, cu);
            List<String> usedTypes = extractUsedTypes(body);
            String returnType = resolveReturnType(node);
            List<String> parameterTypes = resolveParameterTypes(node);
            List<String> thrownExceptions = resolveThrownExceptions(node);

            allInfos.add(new MethodInfo(
                    node, classFqn, category, stmtCount,
                    bodyStart, bodyEnd, methodBody, methodSig,
                    invocations, usedTypes, returnType, parameterTypes, thrownExceptions
            ));
        }

        // Second pass: build ExtractedMethod with sibling info
        for (int i = 0; i < allInfos.size(); i++) {
            MethodInfo info = allInfos.get(i);

            if (info.stmtCount < minStatements && info.category == MethodCategory.NORMAL) {
                continue;
            }
            if (info.category != MethodCategory.NORMAL) {
                continue;
            }

            // Build sibling methods list (all other methods in same type)
            List<SiblingMethod> siblings = new ArrayList<>();
            for (int j = 0; j < allInfos.size(); j++) {
                if (j == i) continue;
                MethodInfo sib = allInfos.get(j);
                siblings.add(new SiblingMethod(
                        sib.methodSig,
                        sib.invocations,
                        sib.usedTypes
                ));
            }

            ExtractedMethod extracted = new ExtractedMethod(
                    filePath, source, classFqn, info.methodSig,
                    info.declaration.getName().getIdentifier(), info.methodBody,
                    info.bodyStart, info.bodyEnd, info.stmtCount, info.category, info.invocations,
                    imports, classFields, supertypes, siblings,
                    info.returnType, info.parameterTypes, info.thrownExceptions, info.usedTypes
            );

            result.add(extracted);
        }
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
