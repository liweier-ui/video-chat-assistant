// 不通过 npm scripts，直接加载 vite 并构建
const { build } = require('./node_modules/vite')

build({
  configFile: './vite.config.ts',
  build: {
    outDir: './dist',
    emptyOutDir: true,
  },
}).then(() => {
  console.log('Build complete!')
  process.exit(0)
}).catch(err => {
  console.error('Build failed:', err)
  process.exit(1)
})
