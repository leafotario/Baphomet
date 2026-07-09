import re
import sys

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Import core_logger if not present
    if "from core.logger import log_exception" not in content:
        # Find the last import
        import_match = list(re.finditer(r'^(?:import|from) .+', content, flags=re.MULTILINE))
        if import_match:
            last_import = import_match[-1]
            insert_pos = last_import.end()
            content = content[:insert_pos] + "\nfrom core.logger import log_exception\n" + content[insert_pos:]

    # Replace except Exception: \n LOGGER.error(..., exc_info=True)
    # We want to change except Exception: to except Exception as exc: and call log_exception(exc)
    
    # Simple regex approach: find except Exception: or except Exception as X:
    # and replace the LOGGER.error(..., exc_info=True) inside it.
    
    # regex logic:
    # except Exception(?: as (\w+))?: matches the except statement. We also allow capturing the variable.
    # (?:\s+.*?\n)*? matches intermediate code before LOGGER.error
    # \s+LOGGER\.error\(\s*(["\'][^"\']+["\'])(?:.*?)exc_info=True,?\s*\) matches the LOGGER.error call.
    pattern = r'(except Exception(?: as (\w+))?:(?:\s+.*?\n)*?\s+)(?:[A-Z_]*LOGGER|logging)\.error\(\s*(["\'][^"\']+["\'])(?:.*?)exc_info=True,?\s*\)'
    
    def replacer(match):
        prefix = match.group(1)
        var_name = match.group(2)
        msg = match.group(3)
        
        if not var_name:
            # We need to change 'except Exception:' to 'except Exception as exc:'
            prefix = prefix.replace('except Exception:', 'except Exception as exc:', 1)
            var_name = 'exc'
            
        indent = prefix.split('\n')[-1]
        
        return f'{prefix}log_exception({var_name}, context={msg})'
        
    new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)
    
    # Additionally, some use logging.exception(...) or logger.exception(...)
    # Let's catch those too
    pattern_exc = r'(except Exception(?: as (\w+))?:(?:\s+.*?\n)*?\s+)(?:self\.)?(?:logger|LOGGER|logging)\.exception\(\s*(["\'][^"\']+["\'])\s*\)'
    def replacer_exc(match):
        prefix = match.group(1)
        var_name = match.group(2)
        msg = match.group(3)
        
        if not var_name:
            prefix = prefix.replace('except Exception:', 'except Exception as exc:', 1)
            var_name = 'exc'
            
        return f'{prefix}log_exception({var_name}, context={msg})'

    new_content = re.sub(pattern_exc, replacer_exc, new_content, flags=re.DOTALL)

    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Processed {filepath}")
    else:
        print(f"No changes in {filepath}")

files = [
    "/home/romeu/Documentos/Baphomet/database.py",
    "/home/romeu/Documentos/Baphomet/core_db_transaction.py",
    "/home/romeu/Documentos/Baphomet/core_redis_state.py",
    "/home/romeu/Documentos/Baphomet/movie_logic.py",
    "/home/romeu/Documentos/Baphomet/tmdb_api.py",
]

for f in files:
    process_file(f)
