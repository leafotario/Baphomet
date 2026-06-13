import os
import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    original_content = content

    # Padrões comuns:
    # 1. print(e) ou print(f"Erro: {e}")
    # 2. LOGGER.exception(...) ou self.logger.error(...)
    # 3. pass
    
    # Vamos focar em except Exception: e except Exception as e:
    
    def replacer(match):
        prefix = match.group(1) # except Exception(?: as \w+)?:
        var_name = match.group(2)
        inner_content = match.group(3) # conteúdo dentro do except
        
        # Se inner_content contiver "return", "continue", "break", ou lógicas específicas que não queremos quebrar,
        # Nós apenas INJETAMOS o log_exception ANTES do inner_content, e removemos os prints/logs nativos se houver.
        
        if not var_name:
            prefix = prefix.replace('except Exception:', 'except Exception as exc:', 1)
            var_name = 'exc'
            
        # Tentar remover prints/loggers
        cleaned_inner = re.sub(r'^[ \t]*(?:print|logger\.error|logger\.exception|LOGGER\.error|LOGGER\.exception|self\.logger\.error|self\.logger\.exception)\(.*?\)\n?', '', inner_content, flags=re.MULTILINE)
        
        # Se ficou só espaço em branco ou pass
        if not cleaned_inner.strip() or cleaned_inner.strip() == 'pass':
            indent = prefix.split('\n')[-1].replace('except Exception as ' + var_name + ':', '').replace('except Exception:', '') + "    "
            return f"{prefix}\n{indent}log_exception({var_name})\n"
            
        else:
            indent = prefix.split('\n')[-1].replace('except Exception as ' + var_name + ':', '').replace('except Exception:', '') + "    "
            return f"{prefix}\n{indent}log_exception({var_name})\n{cleaned_inner}"


    # Regex to match except block:
    # except Exception(?: as (\w+))?:(.*?) (?=except|finally|else|def|async def|class|@[a-zA-Z]|\Z|^\s*$)
    # We can't easily parse Python with regex for blocks, so let's just find the first statement.
    # Better: let's just look for lines with except Exception and inject right after it.
    
    lines = content.split('\n')
    new_lines = []
    changed = False
    
    for i, line in enumerate(lines):
        new_lines.append(line)
        if re.match(r'^\s*except Exception\s*:', line):
            indent = re.match(r'^(\s*)', line).group(1) + "    "
            new_lines[-1] = line.replace('except Exception:', 'except Exception as exc:')
            new_lines.append(f"{indent}log_exception(exc)")
            changed = True
        elif re.match(r'^\s*except Exception as (\w+)\s*:', line):
            indent = re.match(r'^(\s*)', line).group(1) + "    "
            var = re.match(r'^\s*except Exception as (\w+)\s*:', line).group(1)
            new_lines.append(f"{indent}log_exception({var})")
            changed = True

    if changed:
        new_content = '\n'.join(new_lines)
        # Import core_logger se não tiver
        if "from core_logger import log_exception" not in new_content:
            import_match = list(re.finditer(r'^(?:import|from) .+', new_content, flags=re.MULTILINE))
            if import_match:
                last_import = import_match[-1]
                insert_pos = last_import.end()
                new_content = new_content[:insert_pos] + "\nfrom core_logger import log_exception\n" + new_content[insert_pos:]
        
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Processed: {filepath}")

for root, _, files in os.walk('/home/romeu/Documentos/Baphomet/cogs'):
    for file in files:
        if file.endswith('.py'):
            process_file(os.path.join(root, file))

