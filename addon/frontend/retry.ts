// 工具函数，用于延迟一段时间
const sleep = (time: number) => {
  return new Promise((resolve) => {
    setTimeout(resolve, time);
  });
};

// 工具函数，用于包裹 try - catch 逻辑
const awaitErrorWrap = async <T, U = any>(
  promise: Promise<T>
): Promise<[U | null, T | null]> => {
  try {
    const data = await promise;
    return [null, data];
  } catch (err: any) {
    return [err, null];
  }
};

// 重试函数
export const retryRequest = async <T>(
  promise: () => Promise<T>,
  retryTimes: number = 3,
  retryInterval: number = 500
) => {
  let output: [any, T | null] = [null, null];

  for (let a = 0; a < retryTimes; a++) {
    output = await awaitErrorWrap(promise());

    if (output[1]) {
      break;
    }

    console.log(`retry ${a + 1} times, error: ${output[0]}`);
    await sleep(retryInterval);
  }

  return output;
};
