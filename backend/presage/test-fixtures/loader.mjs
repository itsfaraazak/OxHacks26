// Tests only: replace the native SDK so no camera, authentication, or credit
// can be touched while exercising the production bridge process.
export async function resolve(specifier, context, nextResolve) {
  if (specifier === '@smartspectra/node-sdk') {
    return { url: new URL('./sdk.mjs', import.meta.url).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
