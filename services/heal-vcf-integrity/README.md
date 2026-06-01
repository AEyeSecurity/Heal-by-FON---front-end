# HEAL VCF Integrity Validator

Servicio auxiliar para el workflow inicial de HEAL by FON en n8n.

## Objetivo

Validar la integridad tecnica minima de un archivo VCF grande sin cargarlo completo en memoria ni como binario dentro de n8n. n8n solo orquesta: recibe una referencia local al archivo y ejecuta este validador externo.

## Archivos

- `validate_vcf_integrity.py`: validador Python con lecturas por streaming.
- `run_vcf_integrity_check.ps1`: wrapper para n8n Execute Command.
- `samples/valid_small.vcf`: muestra pequena para pruebas controladas.
- `samples/valid_small.vcf.gz`: muestra comprimida generada desde la anterior.
- `samples/invalid_missing_chrom.vcf`: muestra negativa para validar rechazo estructural.

## Entrada esperada

El workflow puede enviar JSON con:

```json
{
  "filePath": "C:\\ServerCIT\\services\\heal-vcf-integrity\\samples\\valid_small.vcf",
  "calculateChecksum": true,
  "calculateStats": true,
  "fullGzipCheck": true,
  "maxVariantsToCheck": 20,
  "vcfParser": "streaming"
}
```

Por seguridad, el validador limita rutas a:

- `C:\ServerCIT\services\heal-vcf-integrity\incoming`
- `C:\ServerCIT\services\heal-vcf-integrity\samples`
- `C:\ServerCIT\n8n\tmp\heal-vcf-integrity`

La integracion futura del frontend deberia subir o registrar archivos en una ruta permitida, o actualizar explicitamente la lista de `allowedRoots` en el workflow.

## Validaciones actuales

- existe y es archivo accesible;
- tamano mayor a cero;
- deteccion de VCF plano o `.vcf.gz` por extension y magic bytes;
- lectura gzip completa por streaming cuando aplica;
- presencia de `##fileformat=VCF`;
- presencia de header `#CHROM`;
- columnas base VCF: `#CHROM POS ID REF ALT QUAL FILTER INFO`;
- primeras variantes con cantidad de columnas esperada, POS entero positivo y CHROM/REF/ALT no vacios;
- SHA-256 por streaming si `calculateChecksum=true`; si no, deja el calculo preparado.
- metricas agregadas por streaming si `calculateStats=true`: filas totales, IDs/rsIDs, PASS, multialelicas, SNV/no-SNV, distribucion de genotipos y top cromosomas/contigs.

## Motor VCF opcional

`vcfParser` acepta:

- `streaming`: motor estable por defecto, sin dependencias nativas.
- `pysam`: intenta usar `pysam.VariantFile`, mas cercano al Colab original.

Si `pysam` no esta instalado o falla al abrir el VCF, el script devuelve un warning y usa `streaming`. En el runtime actual Windows/Python 3.13, `pysam` no tiene wheel disponible y la compilacion desde fuente falla sin toolchain nativa/htslib, por lo que este modo queda preparado para un runtime compatible posterior.

## Prueba manual

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ServerCIT\services\heal-vcf-integrity\run_vcf_integrity_check.ps1 `
  -FilePath C:\ServerCIT\services\heal-vcf-integrity\samples\valid_small.vcf `
  -CalculateChecksum true
```

El resultado debe ser un JSON con `status` igual a `valid`.

Prueba negativa:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ServerCIT\services\heal-vcf-integrity\run_vcf_integrity_check.ps1 `
  -FilePath C:\ServerCIT\services\heal-vcf-integrity\samples\invalid_missing_chrom.vcf
```

El resultado debe ser un JSON con `status` igual a `invalid`, sin romper el workflow de n8n.
